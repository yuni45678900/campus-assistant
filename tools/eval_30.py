# -*- coding: utf-8 -*-
"""
30 条测试集评测脚本（对应技术报告第五章「量化评测 + 消融对比」）

用法：
    python tools/eval_30.py                      # 只跑检索评测（快，不需要 Ollama）
    python tools/eval_30.py --with-llm           # 额外生成回答，算答案正确率 / 来源标注 / 拒答
    python tools/eval_30.py --corpus 演示素材     # 指定语料目录（默认 data/raw）
    python tools/eval_30.py --set tools/eval_set30.json

输出：
    控制台表格 + tools/eval_report.json（含逐题明细，便于复核）

口径说明：
    · 命中：Top-k 片段中出现期望出处文件之一（文件名忽略「某某大学」前缀与扩展名）
    · 五种配置对应技术报告的消融表：
        vector        仅向量
        bm25          仅 BM25
        rrf           双路 + RRF
        rrf_rerank    双路 + RRF + 重排（全库，关闭分类路由）
        routed        分类路由 + 双路 + RRF + 重排（完整链路）
    · 前四种在**全库**上检索；routed 先按路由选中助手再检索，两者之差即分类路由的贡献。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:                                     # Windows 控制台默认 GBK，中文输出会乱码
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app import config                                     # noqa: E402
from app.document import collect_documents                 # noqa: E402
from app.indexer import IndexData, embedder, tokenize      # noqa: E402
from app.retriever import HybridRetriever, reranker        # noqa: E402
from app import assistants as assistants_mod               # noqa: E402
from app import router as router_mod                       # noqa: E402


# --------------------------------------------------------------------------
# 语料与索引
# --------------------------------------------------------------------------
def resolve_corpus(arg: str | None) -> Path:
    if arg:
        p = Path(arg)
        if not p.is_absolute():
            p = (ROOT / p) if (ROOT / p).exists() else p
        return p
    if config.RAW_DIR.exists() and any(config.RAW_DIR.iterdir()):
        return config.RAW_DIR
    demo = ROOT / "演示素材"
    if demo.exists():
        print(f"[提示] data/raw 为空，改用演示语料：{demo}")
        return demo
    raise SystemExit("找不到可用语料：data/raw 与 演示素材 都是空的")


def dedup_chunks(chunks):
    """去掉「某某大学X.docx」这类与 X.docx 内容重复的对照文档片段。

    演示语料里同一份资料常有原件与脱敏副本两版（如「新生助学贷款须知.docx」与
    「某某大学新生助学贷款须知.docx」，正文 1660 / 1664 字）。两版同时入库会各占
    一个 Top3 名额，把评测结果稀释掉，因此默认只保留不带「某某大学」前缀的那版。
    """
    names = {norm_name(c.source) for c in chunks}
    kept = []
    for c in chunks:
        raw = Path(str(c.source)).stem
        if raw.startswith("某某大学") and norm_name(c.source) in names:
            # 有同名不带前缀的版本 → 丢掉副本
            if any(Path(str(k.source)).stem == norm_name(c.source) for k in chunks):
                continue
        kept.append(c)
    return kept


def build_mem_indexes(corpus: Path, dedup: bool = True) -> dict[str, IndexData]:
    from rank_bm25 import BM25Okapi
    import numpy as np

    out: dict[str, IndexData] = {}
    for d in sorted(p for p in corpus.iterdir() if p.is_dir()):
        chunks, report, errors = collect_documents(d, assistant_name=d.name)
        if not chunks:
            print(f"  [跳过] {d.name}：无可解析文档 {list(errors.values())[:1]}")
            continue
        if dedup:
            before = len(chunks)
            chunks = dedup_chunks(chunks)
            if len(chunks) != before:
                print(f"  [去重] {d.name}：{before} → {len(chunks)} 片段（去掉「某某大学」对照副本）")
        t0 = time.time()
        emb = embedder.encode([c.embed_text for c in chunks], batch_size=32, show_progress=False)
        bm25 = BM25Okapi([tokenize(c.embed_text) for c in chunks])
        out[d.name] = IndexData(d.name, chunks, np.asarray(emb), bm25,
                                {"doc_count": len(report), "chunk_count": len(chunks)})
        print(f"  [索引] {d.name:6} {len(report):3} 篇 → {len(chunks):4} 片段 "
              f"({time.time() - t0:.1f}s)")
    if not out:
        raise SystemExit("语料里没有解析出任何片段")
    return out


def merge_indexes(indexes: dict[str, IndexData]) -> IndexData:
    """把多个助手的库合并成一个『全库』，用于关闭分类路由的对照。"""
    from rank_bm25 import BM25Okapi
    import numpy as np

    chunks, embs = [], []
    for data in indexes.values():
        chunks.extend(data.chunks)
        embs.append(np.asarray(data.embeddings))
    return IndexData("__all__", chunks, np.vstack(embs),
                     BM25Okapi([tokenize(c.embed_text) for c in chunks]),
                     {"library": "merged"})


# --------------------------------------------------------------------------
# 五种检索配置
# --------------------------------------------------------------------------
def _vec(data: IndexData, q: str, k: int):
    qv = embedder.encode_query(q)
    return HybridRetriever.vector_search(data, qv, max(k, config.VECTOR_TOP_K))


def _bm25(data: IndexData, q: str, k: int):
    return HybridRetriever.bm25_search(data, q, max(k, config.BM25_TOP_K))


def top_vector(data, q, k=3):
    return [data.chunks[i] for i, _ in _vec(data, q, k)[:k]]


def top_bm25(data, q, k=3):
    return [data.chunks[i] for i, _ in _bm25(data, q, k)[:k]]


def top_rrf(data, q, k=3):
    fused = HybridRetriever.rrf_fuse([_vec(data, q, k), _bm25(data, q, k)])
    order = sorted(fused.items(), key=lambda kv: -kv[1])[:k]
    return [data.chunks[i] for i, _ in order]


def top_rrf_rerank(data, q, k=3):
    fused = HybridRetriever.rrf_fuse([_vec(data, q, k), _bm25(data, q, k)])
    cand = [data.chunks[i] for i, _ in
            sorted(fused.items(), key=lambda kv: -kv[1])[:max(k, config.RERANK_CANDIDATES)]]
    if reranker.available and len(cand) > 1:
        scores = reranker.score(q, [c.text for c in cand])
        cand = [c for c, _ in sorted(zip(cand, scores), key=lambda x: -x[1])]
    return cand[:k]


def top_routed(indexes, assistants, q, k=3):
    """分类路由 → 只在该助手的库里检索（完整链路）。"""
    decision = router_mod.router.route(q, assistants)
    picked = {n: d for n, d in indexes.items() if n in (decision.selected or indexes)}
    if not picked:
        picked = indexes
    hits = HybridRetriever(picked).search(q)
    return [h.chunk for h in hits][:k], decision


# --------------------------------------------------------------------------
# 判分
# --------------------------------------------------------------------------
_PREFIX = re.compile(r"^某某大学")


def norm_name(src: str) -> str:
    name = Path(str(src)).stem
    return _PREFIX.sub("", name).strip()


def hit_files(chunks, expected) -> bool:
    if not expected:
        return False
    exp = {norm_name(e) for e in expected}
    return any(norm_name(c.source) in exp for c in chunks)


def point_hit(answer: str, points) -> tuple[int, int]:
    """返回 (命中的要点数, 要点总数)。points 内部用 | 表示任一命中即可。

    比对前去掉空格与全角空格 —— 文档里「8 月 21 日」这类写法很常见，
    不折一下会把正确答案判成未命中。
    """
    if not points:
        return 0, 0
    a = (answer or "").replace(" ", "").replace("\u3000", "")
    ok = 0
    for p in points:
        if any(k and k.replace(" ", "") in a for k in p.split("|")):
            ok += 1
    return ok, len(points)


# --------------------------------------------------------------------------
# 生成（可选）
# --------------------------------------------------------------------------
REFUSAL_MARKS = ("未包含", "没有相关", "未找到", "没有找到", "无法回答",
                 "不包含", "未提及", "没有提及", "无法确定", "查不到")


async def gen_answers(items, indexes, assistants):
    """按完整链路生成：先路由，再在选中助手的库里混合检索，最后交给模型。"""
    from app.llm import OllamaClient
    from app.prompts import FALLBACK_SYSTEM_PROMPT, build_user_message

    client = OllamaClient()
    answers = {}
    for i, it in enumerate(items, 1):
        try:
            decision = router_mod.router.route(it["q"], assistants)
            picked = {n: d for n, d in indexes.items()
                      if n in (decision.selected or indexes)} or indexes
            hits = HybridRetriever(picked).search(it["q"])
            ctx = HybridRetriever(picked).build_context(hits)
            msgs = [{"role": "system", "content": FALLBACK_SYSTEM_PROMPT.format(context="")},
                    {"role": "user", "content": build_user_message(it["q"], ctx)}]
            ans = await client.chat(msgs)
        except Exception as e:                       # 模型不可用时不阻断评测
            print(f"  [!!] {it['id']} 生成失败：{e}")
            ans = ""
        answers[it["id"]] = (ans or "").strip()
        print(f"  [{i:2}/{len(items)}] {it['id']} {it['q'][:18]}… {len(ans or '')} 字")
    return answers


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=None, help="语料目录，默认 data/raw，缺失时用 演示素材")
    ap.add_argument("--set", default=str(ROOT / "tools" / "eval_set30.json"))
    ap.add_argument("--with-llm", action="store_true", help="额外生成回答，算答案/来源/拒答")
    ap.add_argument("--keep-duplicates", action="store_true",
                    help="保留「某某大学X.docx」对照副本（默认按重复文档丢弃）")
    ap.add_argument("--out", default=str(ROOT / "tools" / "eval_report.json"))
    args = ap.parse_args()

    items = json.loads(Path(args.set).read_text(encoding="utf-8"))["items"]
    corpus = resolve_corpus(args.corpus)
    print(f"测试集：{len(items)} 条 | 语料：{corpus}\n")

    print("构建索引…")
    indexes = build_mem_indexes(corpus, dedup=not args.keep_duplicates)
    merged = merge_indexes(indexes)
    assistants = assistants_mod.discover(corpus)

    # 让路由能拿到内存索引（router 默认读 data/index）
    class _StubStore:
        def get(self, name): return indexes.get(name)
        def get_many(self, names): return {n: indexes[n] for n in names if n in indexes}
        def all(self): return dict(indexes)
    router_mod.store = _StubStore()

    configs = {
        "vector":     lambda q: top_vector(merged, q),
        "bm25":       lambda q: top_bm25(merged, q),
        "rrf":        lambda q: top_rrf(merged, q),
        "rrf_rerank": lambda q: top_rrf_rerank(merged, q),
        "routed":     lambda q: top_routed(indexes, assistants, q)[0],
    }

    results: dict[str, dict] = {}
    for cname, fn in configs.items():
        t0 = time.time()
        rows = []
        for it in items:
            got = fn(it["q"])
            rows.append({
                "id": it["id"], "group": it["group"], "q": it["q"],
                "top1": norm_name(got[0].source) if got else "",
                "top3": [norm_name(c.source) for c in got],
                "hit1": hit_files(got[:1], it["files"]),
                "hit3": hit_files(got[:3], it["files"]),
            })
        ans_items = [r for r in rows if any(it["id"] == r["id"] and it["answerable"] for it in items)]
        results[cname] = {
            "rows": rows,
            "top1": sum(r["hit1"] for r in ans_items) / max(1, len(ans_items)),
            "top3": sum(r["hit3"] for r in ans_items) / max(1, len(ans_items)),
            "sec": round(time.time() - t0, 1),
        }
        print(f"  {cname:11} Top1 {results[cname]['top1']*100:5.1f}%  "
              f"Top3 {results[cname]['top3']*100:5.1f}%  ({results[cname]['sec']}s)")

    # ---------- 分组明细（完整链路） ----------
    print("\n完整链路（routed）按题型：")
    groups = ["高频", "数字", "跨文档"]
    for g in groups:
        sub = [r for r in results["routed"]["rows"]
               if r["group"] == g]
        if sub:
            print(f"  {g:4} {len(sub):2} 题  Top1 {sum(r['hit1'] for r in sub)/len(sub)*100:5.1f}%"
                  f"  Top3 {sum(r['hit3'] for r in sub)/len(sub)*100:5.1f}%")

    # ---------- 可选：生成评测 ----------
    llm_metrics = None
    if args.with_llm:
        print("\n生成回答（调用本地模型，逐题约 4–8 秒）…")
        answers = asyncio.run(gen_answers(items, indexes, assistants))
        ans_ok = src_ok = ref_ok = 0
        n_ans = n_ref = 0
        detail = []
        for it in items:
            a = answers.get(it["id"], "")
            row = {"id": it["id"], "group": it["group"], "answer": a}
            if it["answerable"]:
                n_ans += 1
                got, total = point_hit(a, it["points"])
                passed = total > 0 and got / total >= 0.6
                ans_ok += passed
                exp = {norm_name(f) for f in it["files"]}
                cited = any(norm_name(f) in a or Path(f).stem in a for f in it["files"])
                src_ok += cited
                row.update({"points_hit": got, "points_total": total, "answer_ok": passed,
                            "source_cited": cited})
            else:
                n_ref += 1
                refused = any(m in a for m in REFUSAL_MARKS)
                ref_ok += refused
                row.update({"refused": refused})
            detail.append(row)
        llm_metrics = {
            "answer_acc": ans_ok / max(1, n_ans),
            "source_acc": src_ok / max(1, n_ans),
            "refusal_acc": ref_ok / max(1, n_ref),
            "n_answerable": n_ans, "n_unanswerable": n_ref,
            "detail": detail,
        }
        print(f"  答案正确率 {llm_metrics['answer_acc']*100:.1f}%  "
              f"来源标注正确率 {llm_metrics['source_acc']*100:.1f}%  "
              f"拒答正确率 {llm_metrics['refusal_acc']*100:.1f}%")

    # ---------- 落盘 ----------
    report = {
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "corpus": str(corpus),
        "items": len(items),
        "chunks": {k: len(v) for k, v in indexes.items()},
        "metrics": {k: {"top1": round(v["top1"], 4), "top3": round(v["top3"], 4),
                        "sec": v["sec"]} for k, v in results.items()},
        "rows": {k: v["rows"] for k, v in results.items()},
        "llm": llm_metrics,
    }
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n明细已保存：{args.out}")


if __name__ == "__main__":
    main()
