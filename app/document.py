# -*- coding: utf-8 -*-
"""
文档解析与切片。

校园文档的现实情况：简介/专业介绍/食堂介绍多为 Word，且**信息大量存在于表格里**
（如"窗口-菜品-价格"、"专业-学制-学位"）。而 python-docx 的 paragraphs 属性
读不到表格，直接遍历还会丢失原文档顺序。本模块用 iter_block_items 按 XML 真实
顺序同时取出段落与表格，并把表格序列化成可读文本，避免关键信息丢失。
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterator, List, Optional

from app import config

# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------
@dataclass
class Chunk:
    """一个可检索的最小片段。"""
    id: int
    text: str            # 干净的正文（用于展示 / 引用）
    embed_text: str      # 带标题路径的文本（用于向量化与 BM25）
    source: str          # 来源文件名
    section: str         # 标题路径，如 "三、食堂介绍 > 2. 特色窗口"
    chunk_index: int     # 在本文档内的序号
    assistant: str = ""  # 所属小助手（= data/raw 下的一级目录名）
    images: List[str] = field(default_factory=list)  # 关联图片的访问 URL
    # 图文序列：[{"text": "图书馆介绍…", "images": ["/assets/…/1.jpeg"]}, ...]
    # 保留图片在原文中的位置，前端才能让图紧跟所属词条，而不是堆在一起
    layout: List[dict] = field(default_factory=list)
    is_table: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Chunk":
        return cls(**d)


# --------------------------------------------------------------------------
# Word 解析
# --------------------------------------------------------------------------
# 内嵌图片：把图片从 docx 解压出来，并在正文中插入 [[IMG:url]] 标记。
# 标记跟着文本一起参与切片，所以"图片属于哪一段"天然正确 ——
# 检索命中该段时就能把对应图片一并返回给用户。
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_IMG_MARK_RE = re.compile(r"\[\[IMG:(.*?)\]\]")
# 整句只由图片标记组成（用于把这类片段并回上一句，避免图片脱离所属段落）
_IMG_ONLY_RE = re.compile(r"(?:\[\[IMG:.*?\]\]|\s)+")


def img_mark(url: str) -> str:
    return f"[[IMG:{url}]]"


def safe_name(name: str) -> str:
    """把文档名转成可安全用于目录名与 URL 的形式。"""
    return re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_") or "doc"


def _rels_image_map(zf) -> dict:
    """word/_rels/document.xml.rels → {rId: media 文件名}"""
    from lxml import etree
    try:
        root = etree.fromstring(zf.read("word/_rels/document.xml.rels"))
    except Exception:
        return {}
    out = {}
    for rel in root.iter():
        rid = rel.get("Id")
        target = rel.get("Target") or ""
        if rid and "media/" in target:
            out[rid] = target.split("media/")[-1]
    return out


def extract_doc_images(path: Path) -> dict:
    """
    把 docx 的内嵌图片解压到 data/assets/<文档名>/，返回 {rId: 访问 URL}。
    重复构建时直接覆盖，不会产生垃圾文件。
    """
    import zipfile

    with zipfile.ZipFile(path) as zf:
        rels = _rels_image_map(zf)
        if not rels:
            return {}
        doc_key = safe_name(path.stem)
        dest = config.ASSETS_DIR / doc_key
        mapping = {}
        for rid, fname in rels.items():
            try:
                data = zf.read("word/media/" + fname)
            except KeyError:
                continue
            if len(data) < config.MIN_IMAGE_BYTES:
                continue                      # 装饰性小图标，跳过
            dest.mkdir(parents=True, exist_ok=True)
            (dest / fname).write_bytes(data)
            mapping[rid] = f"/assets/{doc_key}/{fname}"
        return mapping


def para_image_marks(node, img_map: dict) -> str:
    """取出一个段落里内嵌图片对应的标记串。"""
    if not img_map:
        return ""
    marks = []
    for blip in node.iter(f"{{{_A_NS}}}blip"):
        url = img_map.get(blip.get(f"{{{_R_NS}}}embed"))
        if url:
            marks.append(img_mark(url))
    # 每个标记独占一行：切片按标点/换行断句，这样密集的图片标记不会被从中间切断
    return "\n".join(marks)


def _iter_block_items(doc):
    """按文档真实顺序产出 Paragraph / Table（python-docx 原生 API 无法做到）。"""
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from docx.oxml.ns import qn

    body = doc.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, doc)
        elif child.tag == qn("w:tbl"):
            yield Table(child, doc)


_HEADING_RE = re.compile(r"(\d+)")


def _heading_level(paragraph) -> Optional[int]:
    """判断段落是否为标题，返回层级（1~9），否则 None。兼容中英文样式名。"""
    try:
        name = paragraph.style.name or ""
    except Exception:
        return None
    for prefix in ("Heading", "标题", "head", "Head"):
        if name.startswith(prefix):
            if name == "Heading" or name == "标题":   # 无编号，按 1 级处理
                return 1
            m = _HEADING_RE.search(name)
            return int(m.group(1)) if m else 1
    return None


# 部分校园文档是从网页/公众号复制而来，正文里混有 HTML 标签与实体
_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]{0,80}>")


def _clean(text: str) -> str:
    """清理空白、残留 HTML 标签与实体。"""
    text = text.replace("\u3000", " ").replace("\xa0", " ").replace("\u200b", "")
    text = _HTML_TAG_RE.sub("", text)      # 先摘掉真标签
    text = html.unescape(text)             # 再解码实体（此时 &lt; 属于正文内容，保留）
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _table_to_text(table, max_rows: int = 200) -> str:
    """
    把 Word 表格拍平成文本。采用 `表头: 值 | 表头: 值` 的键值对形式，
    比纯 `a | b | c` 网格更利于检索和模型理解（尤其单行多列表）。
    """
    grid: List[List[str]] = []
    for row in table.rows[:max_rows]:
        cells = [_clean(c.text).replace("\n", " ") for c in row.cells]
        # 合并单元格会产生重复文本，简单去重相邻重复
        dedup, prev = [], object()
        for c in cells:
            if c != prev:
                dedup.append(c)
            prev = c
        grid.append(dedup)

    # 去掉全空行/列
    grid = [r for r in grid if any(c for c in r)]
    if not grid:
        return ""

    header, body = grid[0], grid[1:]
    lines: List[str] = []

    if body and len(set(header)) == len(header) and len(header) > 1:
        for row in body:
            pairs = []
            for i, val in enumerate(row):
                if not val:
                    continue
                key = header[i] if i < len(header) and header[i] else f"列{i + 1}"
                pairs.append(f"{key}：{val}")
            if pairs:
                lines.append("；".join(pairs))
    else:
        for row in grid:
            lines.append(" | ".join(c for c in row if c))

    return "\n".join(lines)


def load_docx(path: Path) -> str:
    """
    Word → 文本流，保留标题层级（用 # 标记），并把内嵌图片表示为 [[IMG:url]] 标记。
    图片标记紧跟其所在段落，切片时自然落到对应 chunk 上。
    """
    from docx import Document as DocxDocument

    img_map = extract_doc_images(path)
    doc = DocxDocument(str(path))
    parts: List[str] = []

    for block in _iter_block_items(doc):
        cls_name = type(block).__name__
        if cls_name == "Paragraph":
            text = _clean(block.text)
            marks = para_image_marks(block._element, img_map)
            if not text and not marks:
                continue
            if not text:                              # 纯图片段落
                parts.append(marks)
                continue
            lvl = _heading_level(block)
            prefix = f"\n{'#' * min(lvl, 6)} " if lvl else ""
            parts.append(prefix + text + marks)
        else:  # Table
            tbl_text = _table_to_text(block)
            if tbl_text:
                parts.append(tbl_text)
            marks = para_image_marks(block._element, img_map)
            if marks:
                parts.append(marks)

    return "\n\n".join(parts)


# --------------------------------------------------------------------------
# 兜底解析：绕过 python-docx 的部件加载
# --------------------------------------------------------------------------
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _w(tag: str) -> str:
    return f"{{{_W_NS}}}{tag}"


def _para_text(node) -> str:
    """抽取一个 w:p / w:tc 节点下的全部可见文字（兼容 w:ins、w:smartTag 等嵌套）。"""
    return "".join(t.text or "" for t in node.iter(_w("t")))


def _para_heading_direct(node) -> Optional[int]:
    """从 w:pPr/w:pStyle 判断标题层级。"""
    ppr = node.find(_w("pPr"))
    if ppr is None:
        return None
    style = ppr.find(_w("pStyle"))
    if style is None:
        return None
    val = style.get(_w("val")) or ""
    if "Heading" not in val and "标题" not in val:
        return None
    m = _HEADING_RE.search(val)
    return int(m.group(1)) if m else 1


def load_docx_direct(path: Path) -> str:
    """
    直接解析 word/document.xml，不经过 python-docx。

    部分 .docx（常见于 WPS 导出、内嵌图片较多的文档）关系表畸形，
    python-docx 会抛 "There is no item named 'NULL' in the archive"。
    这条路径只读正文 XML，保证这类文档也能入库，输出格式与 load_docx 一致。
    """
    import zipfile
    from lxml import etree

    with zipfile.ZipFile(path) as z:
        root = etree.fromstring(z.read("word/document.xml"))

    img_map = extract_doc_images(path)
    body = root.find(_w("body"))
    if body is None:
        return ""

    parts: List[str] = []
    for node in body:
        tag = etree.QName(node).localname

        if tag == "p":
            text = _clean(_para_text(node))
            marks = para_image_marks(node, img_map)
            if not text and not marks:
                continue
            if not text:                              # 纯图片段落
                parts.append(marks)
                continue
            lvl = _para_heading_direct(node)
            prefix = f"\n{'#' * min(lvl, 6)} " if lvl else ""
            parts.append(prefix + text + marks)

        elif tag == "tbl":
            rows: List[List[str]] = []
            for tr in node.findall(_w("tr")):
                cells = [_clean(_para_text(tc)).replace("\n", " ")
                         for tc in tr.findall(_w("tc"))]
                if any(cells):
                    rows.append(cells)
            if not rows:
                continue
            header, body_rows = rows[0], rows[1:]
            if body_rows and len(set(header)) == len(header) and len(header) > 1:
                for r in body_rows:
                    pairs = []
                    for i, v in enumerate(r):
                        if not v:
                            continue
                        key = header[i] if i < len(header) and header[i] else "列" + str(i + 1)
                        pairs.append(key + "：" + v)
                    if pairs:
                        parts.append("；".join(pairs))
            else:
                for r in rows:
                    line = " | ".join(c for c in r if c)
                    if line:
                        parts.append(line)

    return "\n\n".join(parts)


def load_text(path: Path) -> str:
    for enc in ("utf-8", "utf-8-sig", "gbk", "gb18030"):
        try:
            return _clean(path.read_text(encoding=enc))
        except UnicodeDecodeError:
            continue
    return _clean(path.read_text(encoding="utf-8", errors="ignore"))


def load_pdf(path: Path) -> str:
    """PDF → 文本（仅支持文字版；扫描件需先 OCR）。"""
    try:
        from pypdf import PdfReader
    except ImportError:
        raise RuntimeError("缺少 pypdf，请执行：pip install pypdf")

    reader = PdfReader(str(path))
    pages = []
    for page in reader.pages:
        t = _clean(page.extract_text() or "")
        if t:
            pages.append(t)
    return "\n\n".join(pages)


def load_any(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".docx":
        try:
            return load_docx(path)
        except Exception as e:
            # python-docx 遇到畸形关系表会失败，降级为直接解析正文 XML
            text = load_docx_direct(path)
            if text.strip():
                return text
            raise RuntimeError(f"解析失败：{e}") from e
    if ext in (".txt", ".md"):
        return load_text(path)
    if ext == ".pdf":
        return load_pdf(path)
    if ext == ".doc":
        raise RuntimeError(
            f"{path.name} 是老版 .doc 格式，python-docx 无法解析。"
            "请用 Word 另存为 .docx 后重试。"
        )
    raise RuntimeError(f"不支持的格式：{ext}")


# --------------------------------------------------------------------------
# 切片
# --------------------------------------------------------------------------
_SENT_END = "。！？；!?;…\n"


def _split_sentences(text: str) -> List[str]:
    """按中文句末标点切句，标点保留在句尾。"""
    sents, buf = [], []
    for ch in text:
        buf.append(ch)
        if ch in _SENT_END:
            s = "".join(buf).strip()
            if s:
                sents.append(s)
            buf = []
    tail = "".join(buf).strip()
    if tail:
        sents.append(tail)

    # 关键：把"只剩图片标记"的句子并回上一句。
    # 否则图片会脱离它所属的段落，被切到相邻 chunk 里去（表现为"图跟不上词条"）。
    merged: List[str] = []
    for s in sents:
        if merged and _IMG_ONLY_RE.fullmatch(s.strip()):
            merged[-1] += s
        else:
            merged.append(s)
    return merged


def _hard_split(long_sent: str, size: int) -> List[str]:
    """超长句（常见于无标点的表格文本）按逗号顿号再切，最后兜底硬切。"""
    pieces: List[str] = []
    for soft in re.split(r"(?<=[，,、）)])", long_sent):
        soft = soft.strip()
        if not soft:
            continue
        if len(soft) <= size:
            pieces.append(soft)
        else:
            # 图片标记不可从中间切断，先保护再还原
            pieces.extend(_split_keeping_marks(soft, size))
    return pieces


def _split_keeping_marks(text: str, size: int) -> List[str]:
    """按 size 硬切，但保证 [[IMG:...]] 标记完整地留在同一片里。"""
    out, buf = [], ""
    for part in re.split(r"(\[\[IMG:.*?\]\])", text):
        if not part:
            continue
        if _IMG_MARK_RE.fullmatch(part):
            if len(buf) + len(part) > size and buf:
                out.append(buf)
                buf = ""
            buf += part
            continue
        while part:
            room = size - len(buf)
            if room <= 0:
                out.append(buf)
                buf, room = "", size
            buf += part[:room]
            part = part[room:]
            if buf and len(buf) >= size:
                out.append(buf)
                buf = ""
    if buf:
        out.append(buf)
    return [p for p in out if p.strip()]


def split_text(text: str,
               size: int = None,
               overlap: int = None) -> List[str]:
    """把长文本切成 size 字左右的片段，尽量在句子边界断开，并保留 overlap 重叠。"""
    size = size or config.CHUNK_SIZE
    overlap = overlap or config.CHUNK_OVERLAP
    text = _clean(text)
    if not text:
        return []
    if len(text) <= size:
        return [text]

    # 1) 切句，并把超长句打散到 <= size
    sents: List[str] = []
    for s in _split_sentences(text):
        sents.extend(_hard_split(s, size) if len(s) > size else [s])

    # 2) 贪心聚合
    chunks: List[str] = []
    cur: List[str] = []
    cur_len = 0
    for s in sents:
        if cur_len + len(s) > size and cur:
            chunks.append("".join(cur).strip())
            # 生成重叠：从已累积句子里由后向前取，凑够 overlap
            tail: List[str] = []
            tlen = 0
            for prev in reversed(cur):
                if tlen + len(prev) > overlap:
                    break
                tail.insert(0, prev)
                tlen += len(prev)
            cur, cur_len = tail, tlen
        cur.append(s)
        cur_len += len(s)

    if cur:
        chunks.append("".join(cur).strip())

    # 3) 清理过短片段：并入前一段
    merged: List[str] = []
    for c in chunks:
        if not c:
            continue
        if merged and len(c) < config.MIN_CHUNK_SIZE:
            merged[-1] = merged[-1] + c
        else:
            merged.append(c)
    return merged


def _split_layout(piece: str) -> List[dict]:
    """
    把片段按图片标记切成「文字段 + 紧跟其后的图片」序列。

    这是"图挂对词条"的关键：切片在文本层面会把相邻词条合并到同一片段，
    但保留图片在原文中的相对位置后，前端就能把图挂在它真正所属的那段文字下面，
    而不是把整个片段的图堆在一起。
    """
    segs: List[dict] = []
    for part in re.split(r"(\[\[IMG:.*?\]\])", piece):
        if not part:
            continue
        m = _IMG_MARK_RE.fullmatch(part.strip())
        if m:
            if segs:
                segs[-1]["images"].append(m.group(1))
            else:
                segs.append({"text": "", "images": [m.group(1)]})
        else:
            txt = part.strip()
            if txt:
                segs.append({"text": txt, "images": []})
    return segs


# --------------------------------------------------------------------------
# 文档 → Chunk 列表
# --------------------------------------------------------------------------
def doc_to_chunks(text: str, source: str, start_id: int = 0,
                  is_table: bool = False, assistant: str = "") -> List[Chunk]:
    """按标题层级切分文档，每个 Chunk 带上「标题路径」与所属助手作为检索上下文。"""
    chunks: List[Chunk] = []
    cur_path: List[str] = []      # 各级标题栈
    cur_lines: List[str] = []
    cid = start_id

    def flush():
        nonlocal cid, cur_lines
        body = "\n".join(cur_lines).strip()
        cur_lines = []
        if not body:
            return
        section = " > ".join(cur_path) if cur_path else ""
        for piece in split_text(body):
            # 从片段里摘出图片标记：标记进 images，正文进 text
            images = _IMG_MARK_RE.findall(piece)
            clean = _IMG_MARK_RE.sub("", piece).strip()
            if not clean and not images:
                continue
            # 关键：保留"哪段文字后面跟着哪几张图"的顺序关系
            layout = _split_layout(piece)
            if not clean:
                # 纯图片片段（如整页截图）：给一句占位说明，
                # 保证它能作为独立证据被检索与展示
                clean = "（此处的信息以图片形式呈现）"
                if not layout:
                    layout = [{"text": clean, "images": list(images)}]
                elif layout[0]["text"] == "":
                    layout[0]["text"] = clean
            # 把助手名与标题路径拼进 embed_text：
            # 既让"窗口价格表"这类孤儿片段能被语义搜到，也让助手名参与路由匹配
            head = f"【{assistant} · {source}" if assistant else f"【{source}"
            prefix = head + (f" | {section}" if section else "") + "】\n"
            chunks.append(Chunk(
                id=cid,
                text=clean,
                embed_text=prefix + clean,
                source=source,
                section=section,
                chunk_index=cid - start_id,
                assistant=assistant,
                images=images,
                layout=layout,
                is_table=is_table,
            ))
            cid += 1

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            flush()
            level = len(m.group(1))
            title = m.group(2).strip()
            cur_path = cur_path[: level - 1] + [title]
            cur_title = title
        else:
            cur_lines.append(line)
    flush()
    return chunks


def collect_documents(raw_dir: Path = None, assistant_name: str = None) -> tuple:
    """
    扫描文档目录，返回 (chunks, 报告, 错误)。

    assistant_name 非空时（分库构建单个助手），所有片段都归入该助手；
    为空时（整库扫描）按 data/raw 下的一级目录名自动判定归属。
    """
    raw_dir = raw_dir or config.RAW_DIR
    files = sorted(
        p for p in raw_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in config.SUPPORTED_EXT
        and not p.name.startswith("~$")          # Word 临时文件
    )

    all_chunks: List[Chunk] = []
    report: dict = {}
    errors: dict = {}

    for path in files:
        rel_path = path.relative_to(raw_dir)
        rel = str(rel_path)
        # 分库构建时用显式指定的助手名；整库扫描时按一级目录名判定
        assistant = assistant_name or (
            rel_path.parts[0] if len(rel_path.parts) > 1 else "通用")
        try:
            text = load_any(path)
        except Exception as e:                     # 单个文件失败不影响整体
            errors[rel] = str(e)
            continue
        if not text.strip():
            errors[rel] = "未提取到任何文本（可能是扫描件/空文档）"
            continue
        doc_chunks = doc_to_chunks(text, source=path.name, start_id=len(all_chunks),
                                   assistant=assistant)
        all_chunks.extend(doc_chunks)
        report[rel] = {"chars": len(text), "chunks": len(doc_chunks),
                       "assistant": assistant}

    return all_chunks, report, errors


if __name__ == "__main__":
    from app import config as cfg
    _chunks, _report, _errors = collect_documents()
    print(f"扫描目录：{cfg.RAW_DIR}")
    for name, info in _report.items():
        print(f"  [OK] {name}  正文 {info['chars']} 字 -> {info['chunks']} 个片段")
    for name, err in _errors.items():
        print(f"  [!!] {name}  {err}")
    print(f"合计片段数：{len(_chunks)}")
