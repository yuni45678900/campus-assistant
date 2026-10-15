# -*- coding: utf-8 -*-
"""
常见问题（FAQ 菜单）自动生成。

思路：**菜单不要手写，直接从知识库长出来。**
    1. 每个助手的索引里，按「文档」把片段归堆；
    2. 把文档名 + 正文开头交给模型，让它为每份资料出一道学生最可能问的问题，
       并给一个 4~8 字的分类名（同类的分类名会被合并成一级分组）；
    3. **用真实检索校验每一道题**：拿这道题去该助手的索引里搜，
       必须能在前几名命中它自己的那份资料，否则丢弃。

为什么要校验：菜单里的题点下去必须答得出来。资料里没有的问题（或只有一张
图片、没有文字的文档）放上菜单，演示时就会回一句「现有资料未包含该信息」——
这里的做法是把「答得出」变成出题的前置条件，不满足的题根本进不了菜单。

产出 data/faq.json：
    {
      "built_at": "2026-10-09 17:20:00",
      "assistants": {
        "招生": {
          "groups": [
            {"c": "录取规则", "items": [{"t": "……？", "m": "短标签", "src": "招生章程.docx"}]}
          ],
          "verified": 12, "dropped": 2
        }
      }
    }
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Callable, Dict, List, Optional, Tuple

from app import config

# 产物路径
FAQ_FILE = config.DATA_DIR / "faq.json"

# --------------------------------------------------------------------------
# 可调参数
# --------------------------------------------------------------------------
MIN_DOC_CHARS = 80      # 文档正文短于这个数就不出题（挡住"只有一张图"的资料）
DIGEST_CHARS = 220      # 每份资料喂给模型的正文长度
BATCH_DOCS = 8          # 一次调用交给模型几份资料
MAX_GROUPS = 6          # 每个助手最多几个一级分组（菜单保持"六组左右"）
MAX_PER_GROUP = 8       # 每个分组下最多几个子问题（够宽，等于"有多少放多少"）
TOP_N_CHECK = 4         # 校验时看检索结果前几名

MERGE_PROMPT = """下面是一个校园问答助手左侧菜单的分类名，括号里是这一类下**已经有的题目**。请把它们归并成 {target} 组，用作菜单的一级分组。

要求：
1. 分类名要短、要具体、各自独立，4~7 个字（例如「报到与材料」「学业与成绩」「奖助与评优」）；
2. **不要给不同分组用同一个词开头**，也不要为了凑名字把两个词硬拼（例如「入学与军训」「入学与缴费」「入学与体检」这种是错的）；
3. **尽量让每组的分组数量均衡**，每组大约 {per} 道题，不要出现"一组十几道、另一组只有一道"；
4. **最多就是 {target} 组**；如果你分出来超过 {target} 组，说明分得太细，请继续合并；
5. **原来每一个分类名都必须出现在结果里**，一个都不能漏，也不要把它们直接丢成一模一样的名字；
6. **语义要相符**：看括号里那些题，归并后的组名必须真能罩住它们。
   例如「审计处负责哪些工作」这种题目，不能塞进「校园活动」；
   如果某一类的题各不相同、找不到共同主题，就把它并进一个题目上真正相近的组里；
7. 只输出一个 JSON 对象，不要解释、不要代码块。

格式：
{"原分类名": "归并后的分类名", ...}

原分类名：
__ITEMS__"""

PROMPT = """你是高校智能问答系统的运营编辑。下面是一批知识库资料，每份给出名字和正文开头。

请为**每一份**资料做四件事：
1. 出一道「学生最可能问、而且这份资料一定能答出来」的问题；
2. 给一个 4~8 个字的分类名（同一批里能归成一类的，必须用**完全相同**的分类名，方便合并）；
3. 给一个 4~6 个字的短标签（放在菜单上，比问题短）；
4. 给这道题起一个**只描述它自己的短语**（6~12 个字，写清问的是哪一年 / 哪个学院 / 哪个专业 / 哪一项）。
   菜单上会把短标签拼在标题前面，所以标签必须**这道题独有**，
   不能出现「这道题也叫『师范专业』、那道题也叫『师范专业』」这种看不出区别的情况。

硬性要求：
- 问题要口语化、具体，像学生随口问的，不超过 25 个字；
- 问题里**不要**出现「资料」「文档」「本文」「根据上述」这类词，也不要加书名号；
- 问题只能问这份资料里**确实写着的内容**，不要问需要推理或外部知识的问题；
- **短标签必须在同一批里互不相同**：每份资料的名字都不一样，就从资料名 / 学院名 / 专业名里取差异点
  （例如「体院专业」「英语专业」「化学专业」，而不是五份全写「师范专业」）；
- 只输出一个 JSON 数组，不要解释、不要 Markdown 代码块。

格式：
[{"doc":"资料名","cat":"分类名","q":"问题","m":"短标签","h":"只描述这道题的短语"}]

资料：
__ITEMS__"""


# --------------------------------------------------------------------------
# 1. 从索引里按文档归堆
# --------------------------------------------------------------------------
def _read_chunks(assistant: str) -> List[dict]:
    f = config.assistant_index_files(assistant)["chunks"]
    if not f.exists():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return []
    return data if isinstance(data, list) else data.get("chunks", [])


def _clean(text: str) -> str:
    """去掉切片里由 docx 标题带出来的 # / ★ 等标记，压掉多余空白。"""
    t = re.sub(r"[#★]+", " ", text or "")
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def doc_blocks(assistant: str) -> List[Tuple[str, str]]:
    """返回 [(文档名, 正文摘要)]，按文档首次出现的顺序。正文太短的会被丢掉。"""
    order: List[str] = []
    buf: Dict[str, List[str]] = {}
    for c in _read_chunks(assistant):
        src = c.get("source") or ""
        if not src:
            continue
        if src not in buf:
            buf[src] = []
            order.append(src)
        buf[src].append(_clean(c.get("text", "")))

    out: List[Tuple[str, str]] = []
    for src in order:
        body = " ".join(x for x in buf[src] if x)
        if len(body) < MIN_DOC_CHARS:
            # 正文太短（多半是"只有一张图"的资料）——不参与出题，
            # 否则会出现一个点进去答不出来的菜单项。
            continue
        out.append((src, body[:DIGEST_CHARS]))
    return out


# --------------------------------------------------------------------------
# 2. 找模型出题
# --------------------------------------------------------------------------
def _scan_objects(text: str) -> List[dict]:
    """从整段文本里把所有**顶层 JSON 对象**抠出来，并保证括号配对。

    为什么不能直接找首尾方括号：模型经常不按 `[{...},{...}]` 输出，而是
    一条一个数组 —— `[{...}]\n[{...}]\n[{...}]`。这时首尾匹配会把整段当成
    一个数组，`json.loads` 直接失败，**整批题目就这么没了**（实测迎新 8 份
    资料因此一道题都没出，菜单只剩 4 个分组）。
    这里改成用深度计数器扫一遍，遇到深度回到 0 的 `}` 就切一个候选对象。
    """
    out: List[dict] = []
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                    except Exception:
                        obj = None
                    if isinstance(obj, dict):
                        out.append(obj)
                    start = -1
    return out


def _parse_array(text: str) -> List[dict]:
    """从模型输出里抠出题目列表。

    容忍三种写法：标准数组 `[{…},{…}]`、一条一个数组 `[{…}][{…}]`、
    以及压根没包数组的裸对象流。只用一种写法去解析会漏掉整批题。
    """
    if not text:
        return []
    s = re.sub(r"```(?:json)?", "", text)

    items: List[dict] = []
    i, j = s.find("["), s.rfind("]")
    if i >= 0 and j > i:
        try:
            arr = json.loads(s[i:j + 1])
        except Exception:
            arr = None
        if isinstance(arr, list):
            items = [x for x in arr if isinstance(x, dict)]
    if not items:                       # 退而求其次：逐个抠对象
        items = _scan_objects(s)
    return [x for x in items if x.get("q")]


def _parse_object(text: str) -> dict:
    """从模型输出里抠出 JSON 对象。"""
    if not text:
        return {}
    s = re.sub(r"```(?:json)?", "", text).strip()
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return {}
    try:
        obj = json.loads(s[i:j + 1])
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _key(name: str) -> str:
    """文档名/分类名归一化：模型抄名字时少个括号、多个空格都能对上。"""
    s = re.sub(r"\.(docx|doc|pdf|txt|md|pptx?|xlsx?)$", "", name or "", flags=re.I)
    return re.sub(r"[\s（）()【】\[\]「」\-—_.,，。、]+", "", s).lower()


def _ask_llm(docs: List[Tuple[str, str]]) -> List[dict]:
    """一批资料 → 一批 {doc, cat, q, m}。失败返回空列表（由调用方降级）。"""
    from app.llm import client

    items = "\n".join(
        f"{n}. {src}\n   {digest}" for n, (src, digest) in enumerate(docs, 1))
    # 一批要输出 BATCH_DOCS 条 JSON，比日常问答长得多；
    # 默认的 num_predict=320 会在半路被截断，导致解析不出 JSON 数组。
    saved = config.LLM_MAX_TOKENS
    config.LLM_MAX_TOKENS = max(saved, 180 * len(docs) + 200)
    try:
        out = asyncio.run(client.chat(
            [{"role": "user", "content": PROMPT.replace("__ITEMS__", items)}]))
    except Exception as e:
        print(f"    [faq] 模型调用失败：{e}")
        return []
    finally:
        config.LLM_MAX_TOKENS = saved
    return _parse_array(out)


def _merge_cats(cats: List[str], counts: Dict[str, int],
                target: int = MAX_GROUPS,
                samples: Optional[Dict[str, List[str]]] = None) -> Dict[str, str]:
    """把碎的分类名归并成 target 组。失败时返回空映射（保持原样）。

    samples：分类名 → 该类下几道题的原文。**必须带上**——只给分类名的话，
    模型无从判断这些题到底讲什么，就会出现「审计处负责什么工作」被塞进
    「校园活动」这种组名与题目对不上的情况。
    """
    if len(cats) <= target:
        return {}
    from app.llm import client

    samples = samples or {}
    total = sum(counts.get(c, 1) for c in cats)
    per = max(2, round(total / max(1, target)))

    lines = []
    for c in cats:
        qs = "；".join(samples.get(c, [])[:3])
        lines.append(f"{c}（{counts.get(c, 1)} 道）{'：' + qs if qs else ''}")
    listed = "\n".join(lines)

    saved = config.LLM_MAX_TOKENS
    config.LLM_MAX_TOKENS = max(saved, 60 * len(cats) + 6 * len(listed) // 4 + 200)
    try:
        out = asyncio.run(client.chat([{
            "role": "user",
            "content": MERGE_PROMPT.replace("{target}", str(target))
                                  .replace("{per}", str(per))
                                  .replace("__ITEMS__", listed)}]))
    except Exception as e:
        print(f"    [faq] 分组归并失败：{e}")
        return {}
    finally:
        config.LLM_MAX_TOKENS = saved
    mapping = _parse_object(out)
    return {k: v for k, v in mapping.items() if isinstance(v, str) and v.strip()}


def _fallback_question(src: str) -> str:
    """模型没给出问题时，拿文档名拼一个（至少说明这份资料讲什么）。"""
    stem = re.sub(r"\.(docx|doc|pdf|txt|md|pptx?|xlsx?)$", "", src, flags=re.I)
    stem = re.sub(r"[（(][^）)]*[）)]\s*$", "", stem).strip(" （()）_-")
    return f"{stem}讲了什么？" if stem else f"{src}讲了什么？"


# 文档名里那些"每份都有"的通用词，剥出来的才是真正能区分资料的限定词
_GENERIC = re.compile(
    r"(学院|大学|学校|专业|介绍|简介|情况|一览|概况|章程|计划|方案|"
    r"本科|普通|招生|新生|入学|指南|通知|文件|资料|汇总|名单|"
    r"某某|我校|本院|本校|\d{4}年|\d{4})")


def _doc_qualifier(src: str) -> str:
    """从文档名里取一个能区分这份资料的限定词，例如「物理与电子工程学院.docx」→「物理与电子工程」。

    取不到（名字全是通用词）就返回空串，由调用方换别的办法。
    """
    stem = re.sub(r"\.(docx|doc|pdf|txt|md|pptx?|xlsx?)$", "", src or "", flags=re.I)
    stem = re.sub(r"[（(][^）)]*[）)]", "", stem)          # 去掉括号里的补充说明
    parts = [p for p in re.split(r"[\s_\-—·]+", stem) if p]
    cand = parts[-1] if parts else stem
    cand = _GENERIC.sub("", cand).strip()
    return cand[:6]


# 问题里能当"限定词"的实体：学院/专业/学年/机构等
_SUBJECT = re.compile(
    r"([\u4e00-\u9fa5]{2,8}(?:学院|学部|专业|系))\s*(?:有哪些|培养|学什么|能做什么|能从事|"
    r"需要掌握|就业|怎么|如何)")


def _subject_of(question: str) -> str:
    """从问题里取"这道题在问谁"，例如「物理学师范专业培养目标是什么」→「物理学师范」。

    学院名会被剝掉后缀（「数学与计算机科学学院」→「数学与计算机科学」），
    太长就砍到 6 个字，拼出来的短标签才不会撑破菜单。
    """
    q = re.sub(r"[？?]+$", "", question or "")
    m = _SUBJECT.search(q)
    subj = (m.group(1) if m else "")
    if not subj:
        # 问题开头本身就是实体（「体育学院有哪些专业」→ 体育学院）
        m2 = re.match(r"^([\u4e00-\u9fa5]{2,10}?(?:学院|专业|系))", q)
        subj = m2.group(1) if m2 else ""
    subj = re.sub(r"(学院|学部|系)$", "", subj)
    # 注意：这里先不截断。截成 6 个字会把「汉语言文学专业」切成
    # 「汉语言文学专」，后面判断"标签是否已被实体包含"就会误判。
    # 长度由 _uniq_tag 统一收口。
    return subj


def _uniq_tag(it: dict, docs: List[Tuple[str, str]]) -> str:
    """给一道题拼一个在同一批里唯一、且能看出区别的短标签。

    菜单上会把短标签放在标题前面（「短标签 标题」），所以它必须"这道题独有"。
    优先级：模型给的 hint → 问题里的实体 → 文档名里剥出的限定词 → 问题开头。
    """
    hint = (it.get("hint") or "").strip()
    if hint:
        return hint[:10]
    q = re.sub(r"[？?]+$", "", it.get("t") or "")
    # 实体尾部的「专业 / 学院 / 系」是通用词，先剥掉再拼，
    # 否则会拼出「汉语言文学专业师范专业」这种叠字。
    subj = re.sub(r"(专业|学院|学部|系)+$", "", _subject_of(q)).strip()
    base = (it.get("raw_m") or "").strip()
    # 实体里若已经含了原标签（「物理学师范」含「师范」），不要再拼一遍。
    if subj and base and base[:2] not in subj:
        return (subj + base)[:10]
    if subj:
        return subj[:8]
    qual = _doc_qualifier(it.get("src", ""))
    if qual and base and base[:2] not in qual:
        return (qual + base)[:10]
    return q[:8] or it.get("src", "")[:8]


def _dedup_suffix(it: dict, docs: List[Tuple[str, str]]) -> str:
    """模型没给标签时的初值：短问题整句，长问题截前 6 个字。"""
    q = re.sub(r"[？?]+$", "", it.get("t") or "")
    return q[:8] or _doc_qualifier(it.get("src", ""))[:8]


# --------------------------------------------------------------------------
# 3. 检索校验
# --------------------------------------------------------------------------
def _make_verifier(assistant: str):
    """返回 check(问题, 文档名) -> bool；索引没就绪时返回 None。"""
    from app.indexer import store
    from app.retriever import HybridRetriever

    data = store.get(assistant)
    if data is None or not data.ready:
        return None
    retriever = HybridRetriever({assistant: data})
    cache: Dict[str, List[str]] = {}

    def check(question: str, source: str) -> bool:
        if question not in cache:
            try:
                hits = retriever.search(question)
            except Exception:
                hits = []
            cache[question] = [h.chunk.source for h in hits[:TOP_N_CHECK]]
        return source in cache[question]

    return check


# --------------------------------------------------------------------------
# 4. 主流程
# --------------------------------------------------------------------------
def _norm(s: str) -> str:
    return re.sub(r"[\s，。？！、,.?!;；:：\"'“”‘’]+", "", s or "")


def build_one(assistant: str, use_llm: bool = True,
              progress: Callable[[str], None] = None) -> Dict:
    """生成单个助手的 FAQ 菜单。"""
    say = progress or (lambda m: None)
    docs = doc_blocks(assistant)
    if not docs:
        return {"groups": [], "verified": 0, "dropped": 0, "docs": 0}

    say(f"  [{assistant}] {len(docs)} 份资料参与出题")

    # 文档名 → 原名（模型抄名字常差一两个括号/空格，用归一化后的键对回去）
    by_key = {_key(src): src for src, _ in docs}

    # ---- 出题（批量；模型漏掉的再单独补一批）----
    raw: List[dict] = []
    if use_llm:
        batches = [docs[i:i + BATCH_DOCS] for i in range(0, len(docs), BATCH_DOCS)]
        for n, batch in enumerate(batches, 1):
            got = _ask_llm(batch)
            say(f"    · 第 {n}/{len(batches)} 批（{len(batch)} 份）→ 出了 {len(got)} 道")
            raw.extend(got)

        covered = {_key(x.get("doc", "")) for x in raw}
        missed = [d for d in docs if _key(d[0]) not in by_key
                  or _key(d[0]) not in covered]
        if missed:
            say(f"    · {len(missed)} 份没出到，再补一轮")
            for n in range(0, len(missed), BATCH_DOCS):
                raw.extend(_ask_llm(missed[n:n + BATCH_DOCS]))

    # ---- 校验：每题必须能在自己的资料上检索到 ----
    verify = _make_verifier(assistant)
    if verify is None:
        say(f"    ⚠ [{assistant}] 索引未就绪，跳过校验")

    kept: List[dict] = []
    dropped = 0
    seen: set = set()
    for x in raw:
        q = (x.get("q") or "").strip()
        src = by_key.get(_key(x.get("doc", "")), "")
        if not q or not src or len(q) > 40:
            dropped += 1
            continue
        if not q.endswith(("？", "?")):
            q += "？"                       # 模型偶尔漏问号
        key = _norm(q)
        if key in seen:                     # 同一助手内去重
            continue
        if verify is not None and not verify(q, src):
            dropped += 1
            continue
        seen.add(key)
        kept.append({"t": q, "raw_m": (x.get("m") or "").strip()[:8],
                     "hint": (x.get("h") or "").strip()[:16],
                     "c": (x.get("cat") or "其他").strip()[:12], "src": src})

    say(f"    ✓ 通过校验 {len(kept)} 道，丢弃 {dropped} 道")

    # ---- 短标签去重：模型给出的标签常常同质（五份不同学院都叫「师范专业」），
    #      菜单上看起来就像同一道题重复了五遍。出现重复的，改用「标题 + 限定词」，
    #      限定词优先取模型写的 hint，其次从文档名里剥出学院名，再次用问题关键词。 ----
    tag_seen: Dict[str, int] = {}
    for it in kept:
        it["m"] = it["raw_m"] or _dedup_suffix(it, docs)
        tag_seen[it["m"]] = tag_seen.get(it["m"], 0) + 1
    for it in kept:
        if tag_seen.get(it["m"], 0) > 1:
            it["m"] = _uniq_tag(it, docs)
    for it in kept:
        it.pop("raw_m", None)

    # ---- 归组：分类名太碎时让模型按题量均衡地归并成六组左右 ----
    cats: List[str] = []
    counts: Dict[str, int] = {}
    samples: Dict[str, List[str]] = {}
    for it in kept:
        if it["c"] not in counts:
            cats.append(it["c"])
            counts[it["c"]] = 0
            samples[it["c"]] = []
        counts[it["c"]] += 1
        samples[it["c"]].append(it["t"])
    if use_llm and len(cats) > MAX_GROUPS:
        mapping = _merge_cats(cats, counts, samples=samples)
        if mapping:
            for it in kept:
                it["c"] = (mapping.get(it["c"]) or it["c"]).strip()[:12]
            say(f"    · 分组由 {len(cats)} 类归并为 "
                f"{len({it['c'] for it in kept})} 组")

    # ---- 每组挑题：题多的组排前面；超出 MAX_GROUPS 的小组并进题最少的组，不丢题 ----
    groups: Dict[str, List[dict]] = {}
    for it in kept:
        groups.setdefault(it["c"], []).append(it)

    ordered = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    head, tail = ordered[:MAX_GROUPS], ordered[MAX_GROUPS:]
    if tail:
        rest = [it for _, items in tail for it in items]
        say(f"    · {len(tail)} 个过小的分组已并入其它组（{len(rest)} 道题）")
        head = [[name, items] for name, items in head]
        for it in rest:
            head.sort(key=lambda kv: len(kv[1]))
            head[0][1].append(it)
        head.sort(key=lambda kv: -len(kv[1]))

    out_groups = []
    for name, items in head:
        picked, used_src = [], set()
        for it in items:
            if it["src"] in used_src:      # 同一份资料在一个组里只出一道
                continue
            used_src.add(it["src"])
            picked.append({"t": it["t"], "m": it["m"], "src": it["src"]})
            if len(picked) >= MAX_PER_GROUP:
                break
        if picked:
            out_groups.append({"c": name, "items": picked})

    return {"groups": out_groups, "verified": len(kept), "dropped": dropped,
            "docs": len(docs)}


def generate(assistants: Optional[List[str]] = None, use_llm: bool = True,
             progress: Callable[[str], None] = None,
             merge: bool = True) -> Dict:
    """生成全部（或指定）助手的常见问题，写入 data/faq.json。"""
    from app.indexer import store

    say = progress or (lambda m: print(m, flush=True))
    store.reload()
    names = assistants or store.built_names()
    if not names:
        say("  没有已建索引的助手，跳过。")
        return {"assistants": {}, "built_at": time.strftime("%Y-%m-%d %H:%M:%S")}

    old = load()
    old_as = (old or {}).get("assistants", {})
    result: Dict[str, dict] = dict(old_as) if assistants else {}

    t0 = time.time()
    for name in names:
        say(f"\n[{name}]")
        result[name] = build_one(name, use_llm=use_llm, progress=say)

    payload = {
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_sec": round(time.time() - t0, 1),
        "assistants": result,
    }
    FAQ_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    total = sum(len(g["items"]) for a in result.values() for g in a.get("groups", []))
    say(f"\n完成：{len(result)} 个助手 / 菜单共 {total} 道题 / 用时 {payload['elapsed_sec']}s")
    return payload


# --------------------------------------------------------------------------
# 5. 读取缓存（供接口用）
# --------------------------------------------------------------------------
_cache: Dict = {"data": None, "ts": 0.0}


def load(force: bool = False) -> Dict:
    if not force and _cache["data"] is not None and time.time() - _cache["ts"] < 30:
        return _cache["data"]
    if not FAQ_FILE.exists():
        data = {"assistants": {}, "built_at": None}
    else:
        try:
            data = json.loads(FAQ_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {"assistants": {}, "built_at": None}
    _cache.update(data=data, ts=time.time())
    return data


def get(assistant: str) -> Optional[Dict]:
    return (load().get("assistants") or {}).get(assistant)


def invalidate() -> None:
    _cache.update(data=None, ts=0.0)
