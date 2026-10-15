# -*- coding: utf-8 -*-
"""
混合检索器：向量召回 + BM25 召回 → RRF 融合 → CrossEncoder 重排。

支持**多助手联合检索**：当路由判定问题跨领域时，会在多个助手的子库里分别检索，
再把候选合并重排。注意各子库的 RRF 分数量纲不可直接比较（它们各自独立排序），
所以多库场景下**必须依赖重排模型给出跨库可比的分数** —— 这也是重排在这里
不只是"提精度"，而是"让跨库融合成立"的原因。

为什么不全用向量：校园问答高频出现「第三食堂」「数学与计算机科学学院」这类专有名词，
稠密向量对生僻专名不敏感，容易张冠李戴；BM25 的词面匹配正好补上这块短板。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from app import config
from app.document import Chunk
from app.indexer import IndexData, embedder, tokenize


@dataclass
class SearchHit:
    """一条检索结果，保留各阶段得分便于排查"为什么这条被召回"。"""
    chunk: Chunk
    score: float                 # 最终得分（重排分优先）
    vector_rank: Optional[int] = None
    bm25_rank: Optional[int] = None
    rrf_score: float = 0.0
    rerank_score: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "id": self.chunk.id,
            "text": self.chunk.text,
            "source": self.chunk.source,
            "section": self.chunk.section,
            "assistant": self.chunk.assistant,
            "images": list(self.chunk.images or []),
            "layout": list(self.chunk.layout or []),
            "score": round(float(self.score), 4),
            "vector_rank": self.vector_rank,
            "bm25_rank": self.bm25_rank,
            "rerank_score": (None if self.rerank_score is None
                             else round(float(self.rerank_score), 4)),
        }


class Reranker:
    """CrossEncoder 重排器（进程内单例）。加载失败自动降级不影响主流程。"""
    _instance: Optional["Reranker"] = None
    _model = None
    _failed = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @property
    def available(self) -> bool:
        return config.ENABLE_RERANK and not self._failed

    def _load(self):
        if self._model is None and not self._failed:
            try:
                from sentence_transformers import CrossEncoder
                from app.models import ensure_model
                path = ensure_model(config.RERANK_MODEL)
                print(f"[rerank] 正在加载重排模型 {config.RERANK_MODEL} ...", flush=True)
                t0 = time.time()
                self._model = CrossEncoder(str(path), device="cpu", max_length=512)
                print(f"[rerank] 加载完成，耗时 {time.time() - t0:.1f}s", flush=True)
            except Exception as e:
                self._failed = True
                print(f"[rerank] 加载失败，已降级为纯融合排序：{e}", flush=True)
        return self._model

    def score(self, query: str, texts: List[str]) -> List[float]:
        model = self._load()
        if model is None:
            return [0.0] * len(texts)
        pairs = [(query, t) for t in texts]
        return [float(s) for s in model.predict(pairs, batch_size=8,
                                                show_progress_bar=False)]

    def warmup(self) -> None:
        self.score("预热", ["预热文本"])


reranker = Reranker()


class HybridRetriever:
    """在若干个助手的子库上做混合检索。"""

    def __init__(self, indexes: Dict[str, IndexData]):
        self.indexes = indexes

    # ---------------- 单库召回 ----------------
    @staticmethod
    def vector_search(data: IndexData, qv, k: int) -> List[Tuple[int, float]]:
        import numpy as np
        sims = data.embeddings @ qv              # 已归一化 → 内积即余弦
        k = min(k, len(sims))
        if k < len(sims):
            idx = np.argpartition(-sims, k - 1)[:k]
            idx = idx[np.argsort(-sims[idx])]
        else:
            idx = np.arange(len(sims))
        return [(int(i), float(sims[i])) for i in idx]

    @staticmethod
    def bm25_search(data: IndexData, query: str, k: int) -> List[Tuple[int, float]]:
        tokens = tokenize(query)
        if not tokens or data.bm25 is None:
            return []
        scores = data.bm25.get_scores(tokens)
        k = min(k, len(scores))
        idx = sorted(range(len(scores)), key=lambda i: -scores[i])[:k]
        return [(int(i), float(scores[i])) for i in idx if scores[i] > 0]

    # ---------------- 融合 ----------------
    @staticmethod
    def rrf_fuse(rankings: List[List[Tuple[int, float]]],
                 k: int = None) -> Dict[int, float]:
        """Reciprocal Rank Fusion：只用名次不用分数，免去两路分数量纲不一致的问题。"""
        k = k or config.RRF_K
        fused: Dict[int, float] = {}
        for ranking in rankings:
            for rank, (idx, _score) in enumerate(ranking, start=1):
                fused[idx] = fused.get(idx, 0.0) + 1.0 / (k + rank)
        return fused

    # ---------------- 主入口 ----------------
    def search(self, query: str,
               rerank_n: int = None,
               use_rerank: bool = None,
               trace: Dict = None) -> List[SearchHit]:
        query = (query or "").strip()
        if not query:
            return []

        live = {n: d for n, d in self.indexes.items() if d.ready}
        if not live:
            return []

        rerank_n = rerank_n or config.RERANK_TOP_N
        if use_rerank is None:
            use_rerank = config.ENABLE_RERANK

        t0 = time.time()
        qv = embedder.encode_query(query)
        t_encode = time.time() - t0

        # 每个助手各取若干候选进入重排，保证多助手协同时不偏科
        per_assistant = max(2, config.RERANK_CANDIDATES // max(1, len(live)))

        hits: List[SearchHit] = []
        per_detail: Dict[str, int] = {}

        for name, data in live.items():
            v_hits = self.vector_search(data, qv, config.VECTOR_TOP_K)
            b_hits = self.bm25_search(data, query, config.BM25_TOP_K)
            v_rank = {i: r for r, (i, _) in enumerate(v_hits, 1)}
            b_rank = {i: r for r, (i, _) in enumerate(b_hits, 1)}
            fused = self.rrf_fuse([v_hits, b_hits])
            order = sorted(fused.items(), key=lambda kv: -kv[1])[:per_assistant]
            per_detail[name] = len(order)
            for idx, rrf in order:
                hits.append(SearchHit(
                    chunk=data.chunks[idx],
                    score=rrf,
                    vector_rank=v_rank.get(idx),
                    bm25_rank=b_rank.get(idx),
                    rrf_score=rrf,
                ))

        if not hits:
            if trace is not None:
                trace.update({"encode_ms": round(t_encode * 1000),
                              "candidates": 0, "per_assistant": per_detail})
            return []

        # 重排：跨库候选在本步获得可比分数
        t1 = time.time()
        if use_rerank and reranker.available and len(hits) > 1:
            try:
                scores = reranker.score(query, [h.chunk.text for h in hits])
                for h, s in zip(hits, scores):
                    h.rerank_score = s
                    h.score = s
                hits.sort(key=lambda h: -h.score)
            except Exception as e:
                print(f"[rerank] 打分失败，回退融合排序：{e}")
        t_rerank = time.time() - t1

        # 截断：优先保证多助手场景下每个助手都有代表
        if len(live) > 1 and hits[0].rerank_score is not None:
            hits = self._pick_per_assistant(hits, rerank_n, live)
        else:
            hits = hits[:rerank_n]

        if hits and hits[0].rerank_score is not None:
            top = hits[0].score
            hits = [h for h in hits if h.score >= max(top - 6.0, -10.0)]

        if trace is not None:
            trace.update({
                "encode_ms": round(t_encode * 1000),
                "candidates": len(hits),
                "per_assistant": per_detail,
                "rerank_ms": round(t_rerank * 1000),
                "rerank_used": bool(use_rerank and reranker.available),
            })
        return hits

    @staticmethod
    def _pick_per_assistant(hits: List[SearchHit], n: int,
                            live: Dict[str, IndexData]) -> List[SearchHit]:
        """按助手轮流取，保证每个参与协同的助手都有片段进入上下文。"""
        buckets: Dict[str, List[SearchHit]] = {k: [] for k in live}
        for h in hits:
            buckets.setdefault(h.chunk.assistant, []).append(h)

        picked: List[SearchHit] = []
        i = 0
        while len(picked) < n:
            added = False
            for name in buckets:
                if i < len(buckets[name]):
                    picked.append(buckets[name][i])
                    added = True
                    if len(picked) >= n:
                        break
            if not added:
                break
            i += 1
        picked.sort(key=lambda h: -h.score)
        return picked

    # ---------------- 上下文拼装 ----------------
    def build_context(self, hits: List[SearchHit]) -> List[dict]:
        """把命中片段整理成给 LLM 的编号资料块（带助手归属，便于模型说明信息来源）。"""
        ctx = []
        for i, h in enumerate(hits, start=1):
            if h.chunk.assistant:
                head = f"[{i}] 来自「{h.chunk.assistant}」助手 —《{h.chunk.source}》"
            else:
                head = f"[{i}] 来源《{h.chunk.source}》"
            if h.chunk.section:
                head += f" / {h.chunk.section}"
            ctx.append({"index": i, "header": head, "text": h.chunk.text})
        return ctx
