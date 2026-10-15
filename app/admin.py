# -*- coding: utf-8 -*-
"""
管理端业务层 —— 知识库运维（统计 / 上传 / 删除 / 助手配置 / 回收站）。

设计原则：**安全约定全部收口在这一层**，路由层只做参数转发与鉴权，
不重复实现任何路径处理逻辑。

  1. 名称收敛：助手名与文件名一律压成「单层名称」——
     丢掉路径分隔符、`..` 与 Windows 非法字符，从源头杜绝目录穿越；
  2. 路径校验：任何写/删之前，目标必须确实位于 data/raw 之下（二次防线）；
  3. 软删除：删除一律移入 data/trash/<时间戳>/，不做真删，可人工还原；
  4. 体积上限：单文件 50 MB（CAMPUS_MAX_UPLOAD_MB 可调）；
  5. 账号登录：页面与接口都走 app/auth.py 的两套账号（学生 / 管理员）。
     另有 CAMPUS_ADMIN_TOKEN 作为无状态旁路口令，留给脚本调用，留空即关闭。

上传提供两条通道，互为兜底：
  · multipart（POST /api/admin/upload）—— 标准通道，需 python-multipart；
  · 裸流（POST /api/admin/upload/raw）—— 零依赖通道，文件名走查询参数。
    服务端没装 python-multipart 时，页面自动降级到这条，否则 FastAPI
    会在「定义路由」阶段就抛错，整个服务都起不来。
"""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from app import config

# --------------------------------------------------------------------------
# 常量与异常
# --------------------------------------------------------------------------
MAX_UPLOAD_BYTES = config.MAX_UPLOAD_MB * 1024 * 1024
TRASH_DIR = config.TRASH_DIR

# Windows 非法文件名字符 + 控制字符
_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
# 匹配开头的相对路径跳转，如 ../../ 或 ..\
_DOTS = re.compile(r"^\.+$")


class AdminError(Exception):
    """管理操作被拒绝（参数非法 / 越界 / 超限）。消息可直接回给前端。"""


# --------------------------------------------------------------------------
# 安全工具
# --------------------------------------------------------------------------
def sanitize_name(raw: str, fallback: str = "未命名") -> str:
    """
    把任意字符串收敛成**单层**安全名称。

    `../../etc/passwd`      -> `passwd`
    `招生/2026\\计划.docx`   -> `计划.docx`
    `..`                    -> `未命名`
    """
    s = (raw or "").strip()
    if not s:
        return fallback
    # 先按两种分隔符切，只保留最后一段（这一步就干掉了所有穿越尝试）
    s = re.split(r"[\\/]+", s)[-1]
    s = _ILLEGAL.sub("", s)
    # Windows 不允许文件名以点或空格结尾
    s = s.strip(". ")
    if not s or _DOTS.match(s):
        return fallback
    if len(s) > 120:                      # 保留扩展名做截断
        stem, dot, ext = s.rpartition(".")
        s = (stem[: 120 - len(ext) - 1] + dot + ext) if dot else s[:120]
    return s


def ensure_within(path: Path, base: Path) -> Path:
    """二次防线：确认 path 解析后确实落在 base 之内，否则拒绝。"""
    p = Path(path).resolve()
    b = Path(base).resolve()
    if p != b and b not in p.parents:
        raise AdminError(f"路径越界，已拒绝：{path}")
    return p


def assistant_dir(name: str, create: bool = False) -> Path:
    """取助手目录（助手名经过收敛 + 校验）。"""
    clean = sanitize_name(name, fallback="")
    if not clean:
        raise AdminError("助手名不能为空")
    d = config.RAW_DIR / clean
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return ensure_within(d, config.RAW_DIR)


def check_extension(filename: str) -> str:
    """
    校验扩展名并返回小写形式。不可解析的老格式（.doc）直接拒收。
    这是「方案 A」：干净、不误导 —— 与其让它传上来却永远进不了索引，
    不如当场告诉用户怎么办。
    """
    ext = Path(sanitize_name(filename)).suffix.lower()
    if not ext:
        raise AdminError("文件没有扩展名，无法识别格式")
    if ext == ".doc":
        raise AdminError(
            "不支持老版 .doc 格式（解析器无法读取）。"
            "请用 Word 打开后「另存为 .docx」再上传。"
        )
    if ext not in config.PARSABLE_EXT:
        allow = "、".join(sorted(config.PARSABLE_EXT))
        raise AdminError(f"不支持的格式 {ext}，仅接受：{allow}")
    return ext


def _unique_dir(base: Path, stamp: str) -> Path:
    """同一秒内多次删除时避免目录重名。"""
    d = base / stamp
    i = 2
    while d.exists():
        d = base / f"{stamp}-{i}"
        i += 1
    return d


# --------------------------------------------------------------------------
# 分块落盘器
# --------------------------------------------------------------------------
class DocSink:
    """
    边收边写文档，带体积上限与原子替换。

    路由层拿到 upload 流后逐块 write()，全部收完再 commit()；
    中途出错或超限，调用 abort() 清理临时文件即可，不会留下半截垃圾。

    assistant 传空串 = 走**自动归类**：先落到 data/incoming/ 暂存，
    等分类器读完内容再决定放进哪个 data/raw/<分类>/（见 file_into_category）。
    """

    def __init__(self, assistant: str, filename: str, size_hint: int = 0):
        self.assistant = assistant
        self.auto = not (assistant or "").strip()
        self.dir = config.INCOMING_DIR if self.auto else assistant_dir(assistant, create=True)
        self.filename = sanitize_name(filename)
        self.ext = check_extension(self.filename)
        if size_hint and size_hint > MAX_UPLOAD_BYTES:
            raise AdminError(
                f"文件过大（{size_hint / 1048576:.1f} MB），"
                f"上限 {config.MAX_UPLOAD_MB} MB"
            )
        # 校验基准用 DATA_DIR：暂存区和 raw 都在它底下
        self.target = ensure_within(self.dir / self.filename, config.DATA_DIR)
        self.tmp = self.target.with_name(self.target.name + ".part")
        self.written = 0
        self.overwritten = self.target.exists()
        self._fh = open(self.tmp, "wb")

    def write(self, chunk: bytes) -> None:
        if not chunk:
            return
        self.written += len(chunk)
        if self.written > MAX_UPLOAD_BYTES:
            self.abort()
            raise AdminError(
                f"文件超过上限 {config.MAX_UPLOAD_MB} MB，已中止上传"
            )
        self._fh.write(chunk)

    def commit(self) -> Dict:
        self._fh.close()
        if self.written == 0:
            self.abort()
            raise AdminError("上传内容为空")
        self.tmp.replace(self.target)
        return {
            "ok": True,
            "assistant": self.assistant,
            "filename": self.filename,
            "bytes": self.written,
            "kb": round(self.written / 1024, 1),
            "overwritten": self.overwritten,
            "path": str(self.target),
            "parsable": True,
        }

    def abort(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass
        try:
            self.tmp.unlink(missing_ok=True)
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if exc[0] is not None:
            self.abort()
        return False


def save_bytes(assistant: str, filename: str, data: bytes) -> Dict:
    """整块写入（小文件、裸流通道收尾时用）。"""
    with DocSink(assistant, filename, size_hint=len(data)) as sink:
        sink.write(data)
        return sink.commit()


def stage_path(filename: str) -> Path:
    """暂存区里某个文件的目标路径（只算路径，不创建）。"""
    return ensure_within(config.INCOMING_DIR / sanitize_name(filename),
                         config.DATA_DIR)


def file_into_category(staged: Path, category: str) -> Dict:
    """
    把暂存区的文件搬进 data/raw/<分类>/，返回搬运结果。

    分类名走的是和手动上传完全相同的收敛 + 校验链路，
    所以模型就算吐出一个 "../../etc" 也只会变成安全的单层目录名。
    """
    src = ensure_within(staged, config.DATA_DIR)
    if not src.exists() or not src.is_file():
        raise AdminError(f"暂存文件不存在：{src.name}")

    clean = sanitize_name(category, fallback="") or "未分类"
    dst_dir = assistant_dir(clean, create=True)
    dst = ensure_within(dst_dir / src.name, config.RAW_DIR)
    overwritten = dst.exists()

    try:
        src.replace(dst)          # 同盘移动，原子操作
    except OSError:
        shutil.move(str(src), str(dst))

    return {
        "ok": True,
        "assistant": clean,
        "filename": dst.name,
        "bytes": dst.stat().st_size,
        "kb": round(dst.stat().st_size / 1024, 1),
        "overwritten": overwritten,
        "path": str(dst),
        "auto_filed": True,
    }


# --------------------------------------------------------------------------
# 统计
# --------------------------------------------------------------------------
def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _index_meta(assistant: str) -> Dict:
    return _read_json(config.assistant_index_files(assistant)["meta"], {}) or {}


def documents_of(assistant: str) -> List[Dict]:
    """
    某助手下的**磁盘文档清单**（含解析与索引状态）。

    注意这里扫的是 config.SUPPORTED_EXT（含 .doc）而不是 PARSABLE_EXT ——
    目的是让管理端「看得见」那些传不进来/解析不了的文件并给出提示，
    而不是让它们成为隐形文件。
    """
    d = config.RAW_DIR / sanitize_name(assistant, fallback="")
    if not d.is_dir():
        return []

    meta = _index_meta(assistant)
    indexed_files = set((meta.get("files") or {}).keys())
    errors = meta.get("errors") or {}

    out: List[Dict] = []
    for p in sorted(d.rglob("*")):
        if not p.is_file():
            continue
        if p.name.startswith("~$") or p.name.endswith(".part"):
            continue
        ext = p.suffix.lower()
        if ext not in config.SUPPORTED_EXT:
            continue
        rel = str(p.relative_to(d))
        parsable = ext in config.PARSABLE_EXT
        err = errors.get(rel) or errors.get(p.name) or ""
        out.append({
            "name": p.name,
            "rel": rel,
            "ext": ext,
            "kb": round(p.stat().st_size / 1024, 1),
            "mtime": time.strftime("%Y-%m-%d %H:%M",
                                   time.localtime(p.stat().st_mtime)),
            "parsable": parsable,
            "indexed": rel in indexed_files or p.name in indexed_files,
            "error": (err if not parsable else err),
            "hint": "" if parsable else "老版 .doc，无法解析，请另存为 .docx",
        })
    return out


def overview() -> Dict:
    """管理端「概览」视图所需的全部数据。"""
    from app.assistants import discover
    from app.indexer import index_status, store

    reg = discover()
    st = index_status()
    counts = {a["name"]: a for a in st.get("assistants", [])}
    built = set(store.built_names())

    assistants = []
    total_docs = total_chunks = unparsable = 0

    for name, a in reg.items():
        docs = documents_of(name)
        c = counts.get(name, {})
        bad = [d for d in docs if not d["parsable"] or d.get("error")]
        total_docs += len(docs)
        total_chunks += c.get("chunk_count", 0)
        unparsable += len([d for d in bad if not d["parsable"]])
        assistants.append({
            "name": name,
            "display_name": a.display_name,
            "scope": a.scope,
            "doc_count": len(docs),
            "chunk_count": c.get("chunk_count", 0),
            "indexed": name in built,
            "built_at": c.get("built_at"),
            "samples": a.samples,
            "docs": docs,
            "problems": [
                {"name": d["name"], "hint": d.get("hint") or d.get("error")}
                for d in bad
            ],
        })

    return {
        "assistants": assistants,
        "summary": {
            "assistant_count": len(assistants),
            "built_count": len(built),
            "doc_count": total_docs,
            "chunk_count": total_chunks,
            "unparsable_count": unparsable,
            "trash_count": len(list_trash()),
            "raw_dir": str(config.RAW_DIR),
        },
        "config": {
            "llm_model": config.LLM_MODEL,
            "embed_model": config.EMBED_MODEL,
            "rerank_model": config.RERANK_MODEL if config.ENABLE_RERANK else "已关闭",
            "chunk_size": config.CHUNK_SIZE,
            "chunk_overlap": config.CHUNK_OVERLAP,
            "vector_top_k": config.VECTOR_TOP_K,
            "bm25_top_k": config.BM25_TOP_K,
            "rerank_top_n": config.RERANK_TOP_N,
            "route_abs": config.ROUTE_ABS_THRESHOLD,
            "route_margin": config.ROUTE_MARGIN,
            "max_upload_mb": config.MAX_UPLOAD_MB,
            "parsable_ext": sorted(config.PARSABLE_EXT),
            "token_required": bool(config.ADMIN_TOKEN),
            "session_hours": config.SESSION_TTL // 3600,
        },
    }


# --------------------------------------------------------------------------
# 删除 / 回收站
# --------------------------------------------------------------------------
def _stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def delete_document(assistant: str, filename: str) -> Dict:
    """把一份文档移入回收站（不真删），随后由调用方重建该助手索引。"""
    d = assistant_dir(assistant)
    name = sanitize_name(filename)
    src = ensure_within(d / name, config.RAW_DIR)
    if not src.exists():
        raise AdminError(f"文档不存在：{name}")

    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    dest_dir = _unique_dir(TRASH_DIR, f"{_stamp()}-{sanitize_name(assistant)}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest_dir / name))

    return {"ok": True, "assistant": assistant, "filename": name,
            "trashed_to": str(dest_dir / name),
            "trash_dir": str(dest_dir),
            "restore_hint": f"如需还原：把文件移回 {d}"}


def delete_assistant(name: str) -> Dict:
    """删除整个助手：文档进回收站，索引目录一并清掉。"""
    clean = sanitize_name(name, fallback="")
    if not clean:
        raise AdminError("助手名不能为空")

    d = config.RAW_DIR / clean
    moved = None
    if d.is_dir():
        TRASH_DIR.mkdir(parents=True, exist_ok=True)
        dest = _unique_dir(TRASH_DIR, f"{_stamp()}-助手-{clean}")
        dest.mkdir(parents=True, exist_ok=True)
        shutil.move(str(d), str(dest / clean))
        moved = str(dest / clean)

    # 索引目录清掉（可重建，无需回收）
    idx = config.assistant_index_dir(clean)
    if idx.exists():
        ensure_within(idx, config.INDEX_DIR)
        shutil.rmtree(idx, ignore_errors=True)

    # 从 manifest 移除
    manifest = _read_json(config.INDEX_MANIFEST, None)
    if isinstance(manifest, dict):
        (manifest.get("assistants") or {}).pop(clean, None)
        manifest["total_chunks"] = sum(
            v.get("chunk_count", 0)
            for v in (manifest.get("assistants") or {}).values())
        _write_json(config.INDEX_MANIFEST, manifest)

    # 从助手配置移除
    cfg = _read_json(config.ASSISTANTS_FILE, {})
    if isinstance(cfg, dict):
        (cfg.get("assistants") or {}).pop(clean, None)
        _write_json(config.ASSISTANTS_FILE, cfg)

    return {"ok": True, "assistant": clean, "trashed_to": moved}


def list_trash() -> List[Dict]:
    """回收站内容（按时间倒序）。"""
    if not TRASH_DIR.exists():
        return []
    out: List[Dict] = []
    for batch in sorted(TRASH_DIR.iterdir(), reverse=True):
        if not batch.is_dir():
            continue
        files = []
        for p in sorted(batch.rglob("*")):
            if p.is_file():
                files.append({
                    "name": p.name,
                    "rel": str(p.relative_to(batch)),
                    "kb": round(p.stat().st_size / 1024, 1),
                })
        out.append({
            "batch": batch.name,
            "time": time.strftime("%Y-%m-%d %H:%M:%S",
                                  time.localtime(batch.stat().st_mtime)),
            "count": len(files),
            "files": files,
        })
    return out


def purge_trash(batch: str) -> Dict:
    """彻底清空某一批回收站内容（不可逆，前端需二次确认）。"""
    target = ensure_within(TRASH_DIR / sanitize_name(batch, fallback=""), TRASH_DIR)
    if not target.exists():
        raise AdminError(f"回收站批次不存在：{batch}")
    shutil.rmtree(target, ignore_errors=True)
    return {"ok": True, "purged": batch}


# --------------------------------------------------------------------------
# 助手配置
# --------------------------------------------------------------------------
def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_assistant_configs() -> Dict[str, dict]:
    data = _read_json(config.ASSISTANTS_FILE, {})
    if not isinstance(data, dict):
        return {}
    inner = data.get("assistants", data)
    return inner if isinstance(inner, dict) else {}


def upsert_assistant(name: str, display_name: str = "", scope: str = "",
                     description: str = "", samples: Optional[List[str]] = None,
                     create_dir: bool = True) -> Dict:
    """
    新建或更新一个助手的配置。目录不存在时按需创建 ——
    这样「助手配置」里就能直接开一个新助手，再去上传文档即可。
    """
    clean = sanitize_name(name, fallback="")
    if not clean:
        raise AdminError("助手名不能为空")
    if create_dir:
        assistant_dir(clean, create=True)

    cfg = _read_json(config.ASSISTANTS_FILE, {})
    if not isinstance(cfg, dict):
        cfg = {}
    book = cfg.setdefault("assistants", {})
    entry = book.setdefault(clean, {})

    if display_name.strip():
        entry["display_name"] = display_name.strip()[:40]
    if scope.strip():
        entry["scope"] = scope.strip()[:300]
    if description.strip():
        entry["description"] = description.strip()[:2000]
    if samples is not None:
        entry["samples"] = [s.strip()[:120] for s in samples if s and s.strip()][:12]

    cfg["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _write_json(config.ASSISTANTS_FILE, cfg)
    return {"ok": True, "assistant": clean, "entry": entry}


# --------------------------------------------------------------------------
# 与检索链路的衔接
# --------------------------------------------------------------------------
def documents_remaining(assistant: str) -> int:
    """该助手目录下还剩几份可入库文档（判断是否已被删空）。"""
    d = config.RAW_DIR / sanitize_name(assistant, fallback="")
    if not d.is_dir():
        return 0
    return sum(
        1 for p in d.rglob("*")
        if p.is_file() and p.suffix.lower() in config.SUPPORTED_EXT
        and not p.name.startswith("~$")
    )


def drop_assistant_index(assistant: str) -> None:
    """
    删掉某助手的索引目录并从 manifest 除名。

    用在「文档已全部删除」的场景：此时 build_index 会因为没有文档而拒绝构建，
    但旧索引文件仍然留在磁盘上，必须主动清掉，否则检索还会召回已删内容。
    """
    clean = sanitize_name(assistant, fallback="")
    if not clean:
        return
    idx = config.assistant_index_dir(clean)
    if idx.exists():
        ensure_within(idx, config.INDEX_DIR)
        shutil.rmtree(idx, ignore_errors=True)

    manifest = _read_json(config.INDEX_MANIFEST, None)
    if isinstance(manifest, dict):
        (manifest.get("assistants") or {}).pop(clean, None)
        manifest["total_chunks"] = sum(
            v.get("chunk_count", 0)
            for v in (manifest.get("assistants") or {}).values())
        _write_json(config.INDEX_MANIFEST, manifest)


def rebuild(only: Optional[List[str]] = None, with_faq: bool = True) -> Dict:
    """重建索引并让运行中的检索链路立刻感知到变化。"""
    from app.indexer import build_index
    from app.rag import engine

    manifest = build_index(only=only, verbose=False)
    engine.reload()

    # 资料变了，学生端左侧「常见问题」菜单也跟着变——菜单本来就是从这批
    # 资料里长出来的。这一步要调模型，比较慢，所以丢到后台线程里跑，
    # 不挡重建索引的返回；即使失败也只是菜单保持原样，不影响问答。
    if with_faq:
        _schedule_faq(only)
    return manifest


def _schedule_faq(only: Optional[List[str]] = None) -> None:
    import threading

    def run():
        try:
            from app import faq
            faq.generate(assistants=only)
        except Exception as e:
            print(f"[faq] 常见问题自动生成失败（索引已更新，菜单保持原样）：{e}")

    threading.Thread(target=run, name="faq-regen", daemon=True).start()


def reload_only() -> None:
    """只让链路重新加载（不重建索引），用于删除/配置变更后。"""
    from app.rag import engine
    engine.reload()
