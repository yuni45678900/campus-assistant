# -*- coding: utf-8 -*-
"""
分库索引的构建、持久化与加载。

索引结构：**每个小助手一个独立子库**
    data/index/<助手名>/
        embeddings.npy   归一化向量矩阵 (N, D)，内积即余弦相似度
        chunks.json      片段原文 + 元数据（来源 / 章节 / 所属助手）
        bm25.pkl         关键词索引
        meta.json        构建信息
    data/index/manifest.json   全部助手的汇总清单

为什么分库而不是单库加过滤字段：
  1. 单助手可独立重建 —— 改了招生资料不必把另外三个助手一起重跑；
  2. 检索范围天然隔离 —— 问宿舍不该召回招生章程，避免跨域污染；
  3. 每个助手将来可挂不同的嵌入模型或检索参数（预留扩展能力）。
"""
from __future__ import annotations

import json
import pickle
import re
import time
from pathlib import Path
from typing import Dict, List, Optional

from app import config
from app.assistants import discover
from app.document import Chunk, collect_documents

# --------------------------------------------------------------------------
# 中文分词（BM25 用）
# --------------------------------------------------------------------------
STOPWORDS = set("""
的 了 和 是 在 有 我 也 就 都 而 及 与 着 或 一个 没有 我们 你们 他们 它们
这 那 这个 那个 这些 那些 之 于 以 为 对 被 把 让 从 到 向 跟 同 上 下
个 中 里 内 外 前 后 左 右 一 二 三 四 五 六 七 八 九 十 可以 需要 应该
呢 吗 吧 啊 哦 嗯 呀 啦 请 请问 什么 怎么 如何 哪 哪些 谁 时候 一下 一些
""".split())

_PUNCT_RE = re.compile(r"^[\W_]+$")


def tokenize(text: str) -> List[str]:
    """中文分词（搜索引擎模式，提高专有名词召回）。jieba 缺失时退化为字符二元组。"""
    try:
        import jieba
        jieba.setLogLevel(60)
        words = jieba.lcut_for_search(text)
    except ImportError:
        return [text[i:i + 2] for i in range(len(text) - 1)]
    return [w.strip().lower() for w in words
            if w.strip() and not _PUNCT_RE.match(w) and w not in STOPWORDS]


# --------------------------------------------------------------------------
# 嵌入模型（进程内单例）
# --------------------------------------------------------------------------
class Embedder:
    _instance: Optional["Embedder"] = None
    _model = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @property
    def model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            from app.models import ensure_model
            path = ensure_model(config.EMBED_MODEL)
            print(f"[embed] 正在加载嵌入模型 {config.EMBED_MODEL} ...", flush=True)
            t0 = time.time()
            self._model = SentenceTransformer(str(path), device="cpu")
            print(f"[embed] 加载完成，耗时 {time.time() - t0:.1f}s", flush=True)
        return self._model

    @property
    def dim(self) -> int:
        return self.model.get_sentence_embedding_dimension()

    def encode(self, texts: List[str], batch_size: int = 32,
               show_progress: bool = False):
        import numpy as np
        vecs = self.model.encode(
            texts,
            batch_size=batch_size,
            normalize_embeddings=True,      # 归一化后内积 = 余弦相似度
            show_progress_bar=show_progress,
            convert_to_numpy=True,
        )
        return vecs.astype("float32")

    def encode_query(self, query: str):
        """bge 系列官方建议：检索时给 query 加指令前缀，文档侧不加。"""
        return self.encode([config.QUERY_INSTRUCTION + query])[0]


embedder = Embedder()


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------
class IndexData:
    """一个小助手的完整索引。"""

    def __init__(self, assistant: str, chunks: List[Chunk], embeddings,
                 bm25, meta: Dict):
        self.assistant = assistant
        self.chunks = chunks
        self.embeddings = embeddings
        self.bm25 = bm25
        self.meta = meta

    def __len__(self) -> int:
        return len(self.chunks)

    @property
    def ready(self) -> bool:
        return len(self.chunks) > 0 and self.embeddings is not None

    @property
    def centroid(self):
        """该助手全部片段的向量质心，用于意图路由（免额外模型调用）。"""
        import numpy as np
        if not self.ready:
            return None
        c = self.embeddings.mean(axis=0)
        n = np.linalg.norm(c)
        return c / n if n > 1e-8 else c


# --------------------------------------------------------------------------
# 构建
# --------------------------------------------------------------------------
def _write_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def build_assistant_index(name: str, verbose: bool = True) -> Dict:
    """构建单个助手的索引。"""
    import numpy as np
    from rank_bm25 import BM25Okapi

    t_start = time.time()
    src_dir = config.RAW_DIR / name
    chunks, report, errors = collect_documents(src_dir, assistant_name=name)
    if not chunks:
        raise RuntimeError(
            f"助手「{name}」下没有解析到可用文档（{src_dir}）。"
            + (f" 解析失败：{list(errors.values())[:2]}" if errors else "")
        )

    if verbose:
        for fname, info in report.items():
            print(f"    [OK] {fname}  {info['chars']} 字 -> {info['chunks']} 片段")
        for fname, err in errors.items():
            print(f"    [!!] {fname}  {err[:80]}")

    t0 = time.time()
    embeddings = embedder.encode([c.embed_text for c in chunks],
                                 batch_size=32, show_progress=False)
    t_embed = time.time() - t0

    t0 = time.time()
    bm25 = BM25Okapi([tokenize(c.embed_text) for c in chunks])
    t_bm25 = time.time() - t0

    files = config.assistant_index_files(name)
    files["dir"].mkdir(parents=True, exist_ok=True)

    meta = {
        "assistant": name,
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "chunk_count": len(chunks),
        "doc_count": len(report),
        "embed_model": config.EMBED_MODEL,
        "dim": int(embeddings.shape[1]),
        "chunk_size": config.CHUNK_SIZE,
        "files": report,
        "errors": errors,
        "elapsed": {
            "embed_sec": round(t_embed, 1),
            "bm25_sec": round(t_bm25, 1),
            "total_sec": round(time.time() - t_start, 1),
        },
    }

    # 原子落盘
    # np.save 传路径时会给非 .npy 结尾的名字自动补后缀，必须传文件句柄
    tmp = files["embeddings"].with_name(files["embeddings"].name + ".tmp")
    with open(tmp, "wb") as f:
        np.save(f, embeddings)
    tmp.replace(files["embeddings"])

    _write_json(files["chunks"], [c.to_dict() for c in chunks])
    _write_json(files["meta"], meta)

    tmp = files["bm25"].with_name(files["bm25"].name + ".tmp")
    with open(tmp, "wb") as f:
        pickle.dump(bm25, f, protocol=4)
    tmp.replace(files["bm25"])

    if verbose:
        print(f"    -> {len(chunks)} 片段，耗时 {meta['elapsed']['total_sec']}s")
    return meta


def build_index(only: List[str] = None, verbose: bool = True) -> Dict:
    """
    构建索引。only 为空时重建全部助手。
    单个助手失败不影响其它助手。
    """
    t0 = time.time()
    reg = discover()
    if not reg:
        # 知识库被清空是**合法状态**（演示前清空、或把资料全删了），
        # 不该当错误。写一份空 manifest 把索引进度同步过去即可，
        # 否则旧索引会赖在内存里，学生端显示"有 26 个片段"但其实一个字都没有。
        store.reload()
        manifest = {
            "assistants": {},
            "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total_chunks": 0,
            "total_sec": round(time.time() - t0, 1),
        }
        config.INDEX_MANIFEST.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        if verbose:
            print("  [build] data/raw 下没有任何资料，索引已重置为空。")
        return manifest

    targets = [n for n in reg if not only or n in only]
    if not targets:
        raise RuntimeError(f"未找到指定的分类：{only}；现有：{list(reg)}")

    manifest = load_manifest() or {"assistants": {}}
    manifest.setdefault("assistants", {})
    results: Dict[str, dict] = {}

    for name in targets:
        if verbose:
            print(f"\n  [{name}] 开始构建 ...", flush=True)
        try:
            meta = build_assistant_index(name, verbose=verbose)
            manifest["assistants"][name] = {
                "chunk_count": meta["chunk_count"],
                "doc_count": meta["doc_count"],
                "built_at": meta["built_at"],
                "elapsed_sec": meta["elapsed"]["total_sec"],
                "files": list(meta["files"].keys()),
            }
            results[name] = meta
        except Exception as e:
            print(f"    [失败] {name}：{e}", flush=True)
            results[name] = {"error": str(e)}

    manifest["built_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    manifest["total_chunks"] = sum(
        v.get("chunk_count", 0) for v in manifest["assistants"].values())
    manifest["total_sec"] = round(time.time() - t0, 1)
    _write_json(config.INDEX_MANIFEST, manifest)
    return manifest


# --------------------------------------------------------------------------
# 加载
# --------------------------------------------------------------------------
def load_manifest() -> Optional[Dict]:
    if not config.INDEX_MANIFEST.exists():
        return None
    try:
        return json.loads(config.INDEX_MANIFEST.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_assistant_index(name: str) -> Optional[IndexData]:
    """读取某个助手的索引；不存在或损坏时返回 None。"""
    import numpy as np

    files = config.assistant_index_files(name)
    need = [files["embeddings"], files["chunks"], files["bm25"], files["meta"]]
    if not all(p.exists() for p in need):
        return None
    try:
        embeddings = np.load(files["embeddings"])
        chunks = [Chunk.from_dict(d) for d in
                  json.loads(files["chunks"].read_text(encoding="utf-8"))]
        with open(files["bm25"], "rb") as f:
            bm25 = pickle.load(f)
        meta = json.loads(files["meta"].read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[index] 助手「{name}」索引读取失败：{e}")
        return None

    if len(chunks) != embeddings.shape[0]:
        print(f"[index] 助手「{name}」索引数据不一致，需重建")
        return None
    return IndexData(name, chunks, embeddings, bm25, meta)


class IndexStore:
    """分库索引的统一入口：懒加载 + 缓存 + 文件变更自动重载。"""

    def __init__(self):
        self._cache: Dict[str, IndexData] = {}
        self._mtime: Dict[str, float] = {}

    @staticmethod
    def _mtime_of(name: str) -> float:
        p = config.assistant_index_files(name)["meta"]
        return p.stat().st_mtime if p.exists() else 0.0

    def built_names(self) -> List[str]:
        """已经构建好索引的助手名（按 manifest 顺序，未登记的按目录扫描补齐）。"""
        manifest = load_manifest() or {}
        names = list((manifest.get("assistants") or {}).keys())
        for d in sorted(config.INDEX_DIR.iterdir()) if config.INDEX_DIR.exists() else []:
            if d.is_dir() and (d / "meta.json").exists() and d.name not in names:
                names.append(d.name)
        return [n for n in names if self._mtime_of(n) > 0]

    def get(self, name: str) -> Optional[IndexData]:
        if name not in self._cache or self._mtime.get(name) != self._mtime_of(name):
            data = load_assistant_index(name)
            if data is not None:
                self._cache[name] = data
                self._mtime[name] = self._mtime_of(name)
        return self._cache.get(name)

    def get_many(self, names: List[str]) -> Dict[str, IndexData]:
        out = {}
        for n in names:
            d = self.get(n)
            if d is not None:
                out[n] = d
        return out

    def all(self) -> Dict[str, IndexData]:
        return self.get_many(self.built_names())

    def reload(self) -> None:
        self._cache.clear()
        self._mtime.clear()


store = IndexStore()


def index_status() -> Dict:
    """给前端用的索引状态摘要。"""
    manifest = load_manifest() or {}
    assistants = manifest.get("assistants") or {}
    built = store.built_names()
    return {
        "ready": len(built) > 0,
        "assistant_count": len(built),
        "assistants": [
            {
                "name": n,
                "chunk_count": assistants.get(n, {}).get("chunk_count", 0),
                "doc_count": assistants.get(n, {}).get("doc_count", 0),
                "built_at": assistants.get(n, {}).get("built_at"),
            }
            for n in built
        ],
        "chunk_count": sum(assistants.get(n, {}).get("chunk_count", 0) for n in built)
                       or sum(len(store.get(n) or []) for n in built),
        "built_at": manifest.get("built_at"),
        "embed_model": config.EMBED_MODEL,
    }


if __name__ == "__main__":
    build_index()
