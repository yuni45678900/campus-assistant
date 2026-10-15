# -*- coding: utf-8 -*-
"""
Ollama 客户端（Qwen3-8B，GPU 推理，模型常驻显存）。

选 Ollama 而不是 transformers 的原因：它自带 GGUF 量化模型分发、自带 CUDA
后端（自动把整模型 offload 到显存），以及 keep_alive 常驻机制——
本机实测 qwen3:8b 的 Q4_K_M 约 5.4GB，可完整放进 RTX 4060 的 8GB 显存，
因此首字延迟与生成速度都远快于 CPU 方案。

关键调优：
  1. think=false —— 关掉 Qwen3 的思考链，首字延迟直接减半；
  2. num_ctx 收紧到 3072 —— KV cache 更小，prompt 处理更快；
  3. num_predict 限制输出长度 —— 控制在 320 token 内，生成更快；
  4. keep_alive=-1 —— 模型常驻显存，避免空闲卸载后首条消息重新加载的卡顿。
"""
from __future__ import annotations

import json
import re
from typing import AsyncGenerator, Dict, List, Optional

import httpx

from app import config

# 兜底：万一思考链漏在正文里，把标签连同内容一起摘掉
_THINK_BLOCK_RE = re.compile(r"<\s*think\s*>.*?<\s*/\s*think\s*>", re.I | re.S)
_THINK_TAG_RE = re.compile(r"<\s*/?\s*think\s*>", re.I)


class OllamaError(RuntimeError):
    """Ollama 相关的可预期错误（服务未启动、模型缺失、超时等）。"""


class OllamaClient:
    def __init__(self, host: str = None, model: str = None):
        self.host = (host or config.OLLAMA_HOST).rstrip("/")
        self.model = model or config.LLM_MODEL

    # ---------------- 健康检查 ----------------
    async def list_models(self) -> List[str]:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{self.host}/api/tags")
                r.raise_for_status()
                return [m.get("name", "") for m in r.json().get("models", [])]
        except Exception as e:
            raise OllamaError(
                f"无法连接 Ollama（{self.host}），请确认 Ollama 正在运行。原始错误：{e}"
            ) from e

    async def health(self) -> Dict:
        """返回 {ok, running, model_ready, models, message}"""
        try:
            models = await self.list_models()
        except OllamaError as e:
            return {"ok": False, "running": False, "model_ready": False,
                    "models": [], "message": str(e)}

        base = self.model.split(":")[0]
        ready = any(m == self.model or m.startswith(base) for m in models)
        return {
            "ok": True,
            "running": True,
            "model_ready": ready,
            "models": models,
            "message": ("模型已就绪" if ready
                        else f"Ollama 已运行，但未检测到模型 {self.model}，"
                             f"请执行：ollama pull {self.model}"),
        }

    # ---------------- 请求体 ----------------
    def _payload(self, messages: List[Dict], stream: bool,
                 send_think_flag: bool) -> Dict:
        # keep_alive 让模型常驻显存，避免空闲后被卸载、首条消息重新加载的卡顿。
        # Ollama API 要求：纯数字必须传 int（传字符串 "-1" 会报
        # "time: missing unit in duration"，因为它按 Go duration 解析）；
        # "30m"/"1h" 这类带单位的时长字符串可以原样传。
        keep_alive: Union[int, str] = config.LLM_KEEP_ALIVE
        if isinstance(keep_alive, str) and keep_alive.lstrip("-").isdigit():
            keep_alive = int(keep_alive)
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "keep_alive": keep_alive,
            "options": {
                "temperature": config.LLM_TEMPERATURE,
                "num_ctx": config.LLM_NUM_CTX,
                "num_predict": config.LLM_MAX_TOKENS,
                "top_p": 0.8,
                "repeat_penalty": 1.05,
            },
        }
        if send_think_flag:
            # Ollama 0.9+ 支持该字段；老版本会忽略它，由 prompt 里的 /no_think 兜底
            payload["think"] = config.LLM_THINK
        return payload

    # ---------------- 流式对话 ----------------
    async def chat_stream(self, messages: List[Dict]) -> AsyncGenerator[str, None]:
        """
        流式生成，逐段 yield 增量正文。
        若首次尝试未产出正文（多见于旧版 Ollama 不接受 think 字段），
        自动换用另一种请求体重试一次。
        """
        url = f"{self.host}/api/chat"
        timeout = httpx.Timeout(config.REQUEST_TIMEOUT, connect=10.0)
        attempts = [False] if config.LLM_THINK else [True, False]
        last_error: Optional[Exception] = None

        for send_think_flag in attempts:
            payload = self._payload(messages, stream=True,
                                    send_think_flag=send_think_flag)
            emitted = False
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    async with client.stream("POST", url, json=payload) as resp:
                        if resp.status_code >= 400:
                            body = (await resp.aread()).decode("utf-8", "ignore")
                            last_error = OllamaError(
                                f"Ollama 返回 HTTP {resp.status_code}：{body[:300]}")
                            continue

                        async for line in resp.aiter_lines():
                            if not line.strip():
                                continue
                            try:
                                obj = json.loads(line)
                            except json.JSONDecodeError:
                                continue

                            if obj.get("error"):
                                last_error = OllamaError(f"Ollama 错误：{obj['error']}")
                                break

                            msg = obj.get("message") or {}
                            piece = msg.get("content") or ""
                            # 思考链只可能在 content 里漏出（部分版本），统一摘除
                            if piece and not config.LLM_THINK:
                                piece = _THINK_BLOCK_RE.sub("", piece)
                                piece = _THINK_TAG_RE.sub("", piece)
                            if piece:
                                emitted = True
                                yield piece

                            if obj.get("done"):
                                break

                if emitted:
                    return
            except httpx.ConnectError as e:
                raise OllamaError(
                    f"连接 Ollama 失败（{self.host}）。请先启动 Ollama：ollama serve"
                    f"\n原始错误：{e}"
                ) from e
            except (httpx.ReadTimeout, httpx.WriteTimeout) as e:
                if emitted:
                    return
                raise OllamaError(
                    f"生成超时（{config.REQUEST_TIMEOUT}s）。本地推理较慢，"
                    f"可调小 CAMPUS_LLM_MAX_TOKENS，或改用更小的模型。"
                ) from e

        if last_error:
            raise last_error

    async def chat(self, messages: List[Dict]) -> str:
        """非流式，一次性取回完整回答（命令行调试用）。"""
        parts: List[str] = []
        async for piece in self.chat_stream(messages):
            parts.append(piece)
        return "".join(parts)


client = OllamaClient()


if __name__ == "__main__":
    import asyncio
    print(asyncio.run(client.health()))
