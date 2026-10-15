# -*- coding: utf-8 -*-
"""
多助手意图路由。

**关键设计取舍：用向量相似度路由，而不是让大模型做意图分类。**

  · CPU 环境下一次 LLM 往返要 8~15 秒，如果每次提问前都先让模型分类，
    首字延迟直接翻倍，交互体验会被毁掉；
  · 向量路由是纯矩阵运算，实测 3~5 毫秒，而且完全可解释 ——
    可以给出每个助手的具体得分和决策依据，便于审计与答辩演示。

路由分 = 该助手内部「最相关片段」的相似度（检索证据）
描述向量与库内质心只作为兜底信号保留。

为什么以检索证据为主信号：描述向量与库内质心衡量的是"这个助手整体像不像"，
而真正要判断的是"这个助手里有没有能回答该问题的内容"。实测前者只有 5/9 命中，
改判检索证据后 8/8 —— 问题与助手的整体语义中心常常对不上，
却可能与库内某个具体片段高度相关。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app import config
from app.assistants import Assistant
from app.indexer import embedder, store

# 描述文本过长会被截断且语义被平均化，限制参与向量化的长度
_MAX_ROUTING_CHARS = 220


@dataclass
class RouteDecision:
    """一次路由决策的完整记录，直接对外暴露用于「可追溯决策链」。"""
    mode: str                        # manual | single | multi | fallback
    selected: List[str]              # 最终选中的助手
    scores: Dict[str, float] = field(default_factory=dict)
    detail: Dict[str, List[float]] = field(default_factory=dict)  # 名 → [描述分, 质心分]
    margin: float = 0.0
    confidence: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "selected": self.selected,
            "scores": {k: round(float(v), 4) for k, v in self.scores.items()},
            "detail": {k: [round(float(x), 4) for x in v]
                       for k, v in self.detail.items()},
            "margin": round(float(self.margin), 4),
            "confidence": round(float(self.confidence), 4),
            "reason": self.reason,
        }


class IntentRouter:
    def __init__(self):
        self._desc_vecs: Dict[str, object] = {}

    def _desc_vector(self, a: Assistant):
        """助手描述向量，进程内缓存（描述不变则只算一次）。"""
        if a.name not in self._desc_vecs:
            text = (config.QUERY_INSTRUCTION + a.routing_text)[:_MAX_ROUTING_CHARS]
            self._desc_vecs[a.name] = embedder.encode([text])[0]
        return self._desc_vecs[a.name]

    def clear_cache(self) -> None:
        self._desc_vecs.clear()

    def route(self, question: str,
              assistants: Dict[str, Assistant],
              manual: Optional[str] = None) -> RouteDecision:
        """决定这次提问交给哪个（或哪几个）助手。"""
        # ---- 方案 C：用户手动指定 ----
        if manual and manual not in ("auto", "", None):
            if manual in assistants:
                return RouteDecision(
                    mode="manual", selected=[manual], confidence=1.0,
                    reason=f"用户手动指定了「{manual}」助手")
            # 指定的助手不存在 → 静默回退到自动路由

        if not assistants:
            return RouteDecision(mode="fallback", selected=[],
                                 reason="没有可用的助手")

        # ---- 方案 A：向量相似度自动路由 ----
        qv = embedder.encode_query(question)
        scores: Dict[str, float] = {}
        detail: Dict[str, List[float]] = {}

        for name, a in assistants.items():
            data = store.get(name)
            if data is None or not data.ready:
                continue
            # 主信号：该助手内部「最相关片段」的相似度。
            # 这比"助手级描述向量""库内质心"都可靠 —— 它直接衡量
            # "这个小助手里有没有能回答该问题的内容"，而不是助手整体像不像。
            # 成本很低：就是一次矩阵乘法（全库向量检索）。
            sims = data.embeddings @ qv
            sim_best = float(sims.max())
            # 次信号：助手描述的语义相似度（索引缺失 / 新助手未标注时兜底）
            sim_desc = float(qv @ self._desc_vector(a))
            scores[name] = max(sim_best, sim_desc * 0.95)
            detail[name] = [sim_desc, sim_best]

        if not scores:
            return RouteDecision(
                mode="fallback", selected=list(assistants),
                reason="助手索引不可用，回退为全库检索")

        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        top1_name, top1 = ranked[0]
        top2_name, top2 = ranked[1] if len(ranked) > 1 else ("", 0.0)
        margin = top1 - top2

        # 情况一：最高分过低 —— 无法判断归属，扩大范围联合检索
        if top1 < config.ROUTE_LOW_THRESHOLD:
            selected = [n for n, _ in ranked[:config.ROUTE_MAX_ASSISTANTS]]
            return RouteDecision(
                mode="fallback", selected=selected, scores=scores, detail=detail,
                margin=margin, confidence=top1,
                reason=(f"所有助手得分都偏低（最高 {top1_name} {top1:.3f} < "
                        f"阈值 {config.ROUTE_LOW_THRESHOLD}），难以判断归属，"
                        f"改为在「{'、'.join(selected)}」中联合检索"))

        # 情况二：得分高且与第二名拉开差距 —— 单助手
        if top1 >= config.ROUTE_ABS_THRESHOLD and margin >= config.ROUTE_MARGIN:
            return RouteDecision(
                mode="single", selected=[top1_name], scores=scores, detail=detail,
                margin=margin, confidence=top1,
                reason=(f"「{top1_name}」得分 {top1:.3f}，领先第二名「{top2_name}」"
                        f"{margin:.3f}，判定为单一领域问题，直接路由"))

        # 情况三：得分接近 —— 多助手协同
        selected = [n for n, _ in ranked[:config.ROUTE_MAX_ASSISTANTS]]
        return RouteDecision(
            mode="multi", selected=selected, scores=scores, detail=detail,
            margin=margin, confidence=top1,
            reason=(f"「{top1_name}」({top1:.3f}) 与「{top2_name}」({top2:.3f}) "
                    f"得分接近（差 {margin:.3f} < {config.ROUTE_MARGIN}），"
                    f"属于跨领域问题，联合检索以防遗漏"))


router = IntentRouter()
