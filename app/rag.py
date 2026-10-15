# -*- coding: utf-8 -*-
"""
RAG 主链路：查询改写 → 意图路由 → 多库检索 → 生成 → 决策链留痕。

对外只暴露 answer_stream()，产出结构化事件流，服务层直接转成 SSE。
**每个环节都写进 trace 并随回答返回**，构成可追溯决策链 ——
用户能看见"为什么是这个小助手回答的、依据了哪几段资料"。
"""
from __future__ import annotations

import asyncio
import time
from typing import AsyncGenerator, Dict, List, Optional, Tuple

from app import config
from app.assistants import Assistant, discover
from app.indexer import store
from app.llm import OllamaError, client
from app.prompts import (FALLBACK_SYSTEM_PROMPT, NO_HIT_REPLY, REWRITE_PROMPT,
                         build_user_message)
from app.retriever import HybridRetriever, SearchHit, reranker
from app.router import RouteDecision, router

# 指代词：短句里出现这些词，说明是追问，需要补全上下文
_PRONOUNS = ("它", "他", "这个", "那个", "这些", "那些", "该", "此",
             "那里", "这里", "还有", "另外", "呢", "上面", "刚才")


def resolve_followup(question: str, history: List[Dict]) -> str:
    """
    多轮追问的指代补全（纯规则，不耗模型）：
    "那它几点开门" → "图书馆几点开门 那它几点开门"
    """
    q = (question or "").strip()
    if not history or not q:
        return q
    if len(q) <= 20 and any(p in q for p in _PRONOUNS):
        last_user = next((m["content"] for m in reversed(history)
                          if m.get("role") == "user"), "")
        if last_user:
            return f"{last_user} {q}"
    return q


class RAGEngine:
    def __init__(self):
        self._reg_cache: Optional[Dict[str, Assistant]] = None
        self._reg_ts = 0.0

    # ---------------- 助手注册表 ----------------
    def registry(self, force: bool = False) -> Dict[str, Assistant]:
        """助手注册表（30 秒 TTL 缓存，避免每次请求都扫目录）。"""
        now = time.time()
        if force or self._reg_cache is None or now - self._reg_ts > 30:
            self._reg_cache = discover()
            self._reg_ts = now
        return self._reg_cache

    @property
    def ready(self) -> bool:
        return len(store.built_names()) > 0

    def reload(self) -> None:
        store.reload()
        router.clear_cache()
        self.registry(force=True)

    # ---------------- 查询改写 ----------------
    async def maybe_rewrite(self, question: str,
                            history: List[Dict]) -> Tuple[str, Optional[str]]:
        """
        条件触发的查询改写。返回 (用于检索的查询, 改写结果或 None)。

        实测一次 LLM 往返在 CPU 上要 8~15 秒，所以不做无差别改写：
        先做规则层面的指代补全，只有仍过短时才动用模型。
        """
        merged = resolve_followup(question, history)
        changed = merged if merged != question else None

        if not config.ENABLE_QUERY_REWRITE or len(merged) >= config.REWRITE_MIN_LEN:
            return merged, changed

        try:
            out = await client.chat(
                [{"role": "user", "content": REWRITE_PROMPT.format(question=merged)}])
            out = out.strip().strip('"').strip("'").split("\n")[0].strip()
            if 0 < len(out) <= 120:
                return out, out
        except Exception as e:
            print(f"[rewrite] 改写失败，沿用原问题：{e}")
        return merged, changed

    # ---------------- 检索 ----------------
    @staticmethod
    def _search(indexes, query: str):
        retriever = HybridRetriever(indexes)
        trace: Dict = {}
        hits = retriever.search(query, trace=trace)
        return hits, trace

    # ---------------- 生成 ----------------
    def _build_messages(self, question: str, contexts: List[Dict],
                        history: List[Dict], decision: RouteDecision) -> List[Dict]:
        reg = self.registry()

        if len(decision.selected) == 1:
            a = reg.get(decision.selected[0])
            system = (a.build_system_prompt() if a
                      else FALLBACK_SYSTEM_PROMPT.format(context=""))
        else:
            names = "、".join(decision.selected)
            system = (f"你正在以「{names}」这几位助手的身份联合回答问题："
                      f"资料可能来自不同助手，作答时注意区分。\n\n"
                      + FALLBACK_SYSTEM_PROMPT.format(context=""))

        messages: List[Dict] = [{"role": "system", "content": system}]

        # 历史只保留最近若干轮，且压缩长度 —— CPU 上下文越短越快
        for m in history[-config.MAX_HISTORY_TURNS * 2:]:
            role, content = m.get("role"), (m.get("content") or "").strip()
            if role not in ("user", "assistant") or not content:
                continue
            if role == "assistant" and len(content) > 150:
                content = content[:150] + "…"
            messages.append({"role": role, "content": content})

        user_content = build_user_message(question, contexts)
        if config.LLM_FORCE_NO_THINK_PROMPT and not config.LLM_THINK:
            user_content += "\n/no_think"
        messages.append({"role": "user", "content": user_content})
        return messages

    # ---------------- 主入口 ----------------
    async def answer_stream(self, question: str,
                            history: List[Dict] = None,
                            assistant: str = "auto") -> AsyncGenerator[Dict, None]:
        """
        事件类型：
          status   阶段提示（rewrite/route/retrieve/generate）
          rewrite  查询改写结果
          route    路由决策（选中助手、各助手得分、决策理由）
          sources  检索到的片段
          delta    回答增量文本
          done     完成（含耗时与完整决策链 trace）
          error    错误
        """
        history = history or []
        t0 = time.time()
        trace: Dict = {"question": question, "assistant_pref": assistant}

        # ── 1. 查询改写 ───────────────────────────────
        yield {"type": "status", "stage": "rewrite", "data": "正在理解问题…"}
        try:
            query, rewritten = await self.maybe_rewrite(question, history)
        except Exception:
            query, rewritten = question, None
        trace["query"] = query
        trace["rewritten"] = rewritten
        if rewritten and rewritten != question:
            yield {"type": "rewrite",
                   "data": {"original": question, "rewritten": rewritten}}

        # ── 2. 意图路由 ───────────────────────────────
        reg = self.registry()
        if not reg:
            yield {"type": "error",
                   "data": "知识库还是空的，暂时没有可用来回答的资料。\n\n"
                           "请管理员登录管理端，在「上传文档」里放入资料并重建索引，"
                           "之后这里就能正常提问了。"}
            return

        yield {"type": "status", "stage": "route", "data": "正在判断问题归属…"}
        decision = await asyncio.to_thread(router.route, query, reg, assistant)
        trace["route"] = decision.to_dict()
        yield {"type": "route", "data": decision.to_dict()}

        if not decision.selected:
            yield {"type": "delta", "data": NO_HIT_REPLY}
            yield {"type": "done", "data": {"elapsed": round(time.time() - t0, 1),
                                            "no_hit": True, "trace": trace}}
            return

        # ── 3. 多库检索 ───────────────────────────────
        label = "、".join(decision.selected)
        yield {"type": "status", "stage": "retrieve", "data": f"正在检索「{label}」资料…"}
        try:
            indexes = await asyncio.to_thread(store.get_many, decision.selected)
            if not indexes:
                yield {"type": "error",
                       "data": f"助手「{label}」的索引不可用，请先运行 build.bat"}
                return
            hits, rtrace = await asyncio.to_thread(self._search, indexes, query)
        except Exception as e:
            yield {"type": "error", "data": f"检索失败：{e}"}
            return

        trace["retrieve"] = rtrace
        trace["hits"] = [h.to_dict() for h in hits]
        yield {"type": "sources", "data": [h.to_dict() for h in hits]}

        if not hits:
            yield {"type": "delta", "data": NO_HIT_REPLY}
            yield {"type": "done", "data": {"elapsed": round(time.time() - t0, 1),
                                            "no_hit": True, "trace": trace}}
            return

        # ── 4. 生成 ──────────────────────────────────
        retriever = HybridRetriever(indexes)
        contexts = retriever.build_context(hits)
        messages = self._build_messages(question, contexts, history, decision)

        yield {"type": "status", "stage": "generate",
               "data": "正在生成回答…"}

        produced = 0
        try:
            async for piece in client.chat_stream(messages):
                produced += len(piece)
                yield {"type": "delta", "data": piece}
        except OllamaError as e:
            yield {"type": "error", "data": str(e)}
            return
        except Exception as e:
            yield {"type": "error", "data": f"生成失败：{e}"}
            return

        if produced == 0:
            yield {"type": "error",
                   "data": f"模型没有返回任何内容。请确认已执行 ollama pull "
                           f"{config.LLM_MODEL}，且服务正常运行。"}
            return

        elapsed = round(time.time() - t0, 1)
        trace["generate"] = {"model": config.LLM_MODEL, "chars": produced,
                             "elapsed": elapsed}
        yield {"type": "done", "data": {"elapsed": elapsed, "chars": produced,
                                        "trace": trace}}


engine = RAGEngine()
