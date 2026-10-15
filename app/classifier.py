# -*- coding: utf-8 -*-
"""
文档自动归类 —— 上传时让模型先读一眼内容，自己决定这份资料归到哪一类。

为什么要有它：演示时最顺手的流程是"把资料拖进去就完事"，
不该逼着人先想"这算招生还是迎新"，然后再去下拉框里挑一个。

判定用的是本地 qwen3，只读文件名 + 正文前一小段，不全文喂进去，
所以一次判断通常在几秒内完成。

**三级兜底**，任何一步失败都往下退，保证一定归得了档：
    1. 模型判断（优先复用已有分类）
    2. 文件名里命中已有分类名
    3. 直接拿文件名当分类名
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Tuple

from app.llm import client as llm

# 喂给模型的正文长度。太长没必要（分类看开头就够了），还拖慢推理。
HEAD_CHARS = 1200

CLASSIFY_PROMPT = """下面是一份校园资料的片段，请判断它属于哪一类，只回答分类名。

规则：
1. 分类名 2~6 个字，概括资料主题，例如：招生、迎新、食堂、宿舍、图书馆、奖助学金。
2. **优先复用已有分类**。如果这份资料是某个已有分类的下位内容
   （例如「专业设置」属于「招生」、「快递地址」属于「后勤服务」），就归入那个已有分类，
   不要因为它更具体就另起一个。
3. 只有确实和已有分类都不沾边时，才新起一个分类名。
4. 只输出分类名本身 —— 不要标点、不要引号、不要解释、不要换行。

已有分类：{existing}

文件名：{filename}

内容片段：
{head}
"""


def _clean_label(raw: str) -> str:
    """把模型的自由发挥收拾成一个能当目录名的短分类名。"""
    s = (raw or "").strip()
    if not s:
        return ""
    first = s.splitlines()[0].strip()
    # 去掉「分类名：xxx」这类前缀
    first = re.sub(r"^(分类名|类别|分类|答案|结果)\s*[:：]\s*", "", first)
    # 只留中英文数字：标点、引号、emoji、空格全部丢掉
    first = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", first)
    return first[:6]


def _head_of(text: str, limit: int = HEAD_CHARS) -> str:
    """取正文开头一小段，压掉多余空白。"""
    s = re.sub(r"\s+", " ", (text or "").strip())
    return s[:limit]


async def classify(filename: str, text: str, existing: List[str]) -> Tuple[str, str]:
    """
    判定这份文档该归到哪一类。

    返回 (分类名, 依据说明)。依据说明是给界面显示用的 ——
    让人知道"为什么放这儿"，而不是被一个黑盒默默归档。
    """
    stem = Path(filename).stem
    existing = [x for x in (existing or []) if x]
    head = _head_of(text)

    if not head and not stem:
        return "未分类", "文件里没有可读内容"

    existing_txt = "、".join(existing) if existing else "（目前还没有任何分类，需要新建一个）"
    prompt = CLASSIFY_PROMPT.format(existing=existing_txt, filename=filename, head=head)

    try:
        raw = await llm.chat([
            {"role": "system", "content": "你是资料归档助手，只输出分类名，不做任何解释。"},
            {"role": "user", "content": prompt},
        ])
        label = _clean_label(raw)
        if label:
            if label in existing:
                return label, f"model: 内容符合已有分类「{label}」"
            return label, f"model: 内容不属于现有分类，新建「{label}」"
    except Exception as e:
        print(f"[classify] 模型判断失败，改用文件名兜底：{e}")

    # 降级一：文件名里出现了已有分类名
    for name in existing:
        if name in stem:
            return name, f"name: 文件名含「{name}」，归入该分类"

    # 降级二：拿文件名当分类
    fallback = _clean_label(stem) or "未分类"
    return fallback, f"name: 按文件名归入「{fallback}」"


def read_head(path: Path) -> str:
    """
    解析文件正文开头（只读文本，不做切块）。
    解析失败返回空串 —— 调用方会退到文件名判定，不会中断上传。
    """
    try:
        from app.document import load_any

        return _head_of(load_any(path))
    except Exception as e:
        print(f"[classify] 解析失败，改用文件名判定：{path.name} ({e})")
        return ""
