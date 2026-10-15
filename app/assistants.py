# -*- coding: utf-8 -*-
"""
小助手注册表。

约定：**data/raw 下的每个一级目录 = 一个小助手**，目录名即助手名。
放一个新文件夹进去，系统就自动多一个小助手，无需改代码。

可在 data/assistants.json 中覆盖描述、示例问题与专属提示词；
未配置时根据该助手下的文档名自动生成描述，保证"放进去就能用"。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from app import config

# --------------------------------------------------------------------------
# 提示词模板
# --------------------------------------------------------------------------
SYSTEM_PROMPT_TEMPLATE = """你是「{display_name}」，{scope}

【回答规则】
1. 只依据下面【参考资料】中的内容回答，不使用资料之外的任何信息。
2. 上面的对话历史只用于理解语境和指代；回答中的**事实细节**（地点、窗口、金额、流程等）
   必须全部来自本次【参考资料】。历史对话与参考资料不一致时，**一律以参考资料为准**。
3. 资料中没有相关内容时，直接回答"现有资料未包含该信息"，不要猜测、不要编造。
4. 涉及时间、地点、金额、分数、比例、电话等具体信息时，必须与资料完全一致，不得改写数字。
5. 回答简洁清晰，控制在 300 字以内；条目较多时用 1. 2. 3. 分点。
6. 回答末尾**单独一行**标注来源，格式是「来源：《这里写你实际依据的文件名.docx》」——
   书名号里必须换成真实的文件名（直接从资料块头部的「来源《…》」里照抄），
   绝对不要原样写出"文件名"三个字。
   如果资料里没有能回答本问题的内容，就**省略这一行**，不要硬标一个来源。
7. 如果用户只是在问候、道谢、告别，或在问你的身份与能力（不是在问校园具体信息），
   就用一两句话自然回应，**不要引用资料、不要标注来源**。
{extra_rules}"""

AUTO_SCOPE_TEMPLATE = "负责解答与「{name}」相关的校园问题。"
AUTO_DESC_TEMPLATE = "{name}：{docs}"


@dataclass
class Assistant:
    """一个小助手的完整定义。"""
    name: str                      # 目录名，如「招生」
    display_name: str              # 展示名，如「招生助手」
    scope: str                     # 职责范围（进提示词）
    description: str               # 用于路由匹配的描述文本
    docs: List[str] = field(default_factory=list)       # 该助手下的文档名
    samples: List[str] = field(default_factory=list)    # 示例问题（辅助路由）
    extra_rules: str = ""          # 追加规则
    chunk_count: int = 0

    # ---------- 路由用文本：助手名 + 职责 + 文档名 + 示例问题 ----------
    @property
    def routing_text(self) -> str:
        parts = [self.display_name, self.description]
        if self.docs:
            parts.append("涵盖资料：" + "、".join(self.docs[:40]))
        if self.samples:
            parts.append("常见问题：" + "；".join(self.samples[:10]))
        return "。".join(p for p in parts if p)

    def build_system_prompt(self, context: str = "") -> str:
        """生成该助手的 system prompt；context 为空时不拼参考资料段（资料走 user 消息）。"""
        extra = f"6. {self.extra_rules}\n" if self.extra_rules.strip() else ""
        prompt = SYSTEM_PROMPT_TEMPLATE.format(
            display_name=self.display_name,
            scope=self.scope,
            extra_rules=extra,
        )
        if context:
            prompt += "\n\n【参考资料】\n" + context
        return prompt


# --------------------------------------------------------------------------
# 发现与注册
# --------------------------------------------------------------------------
def _scan_docs(raw_dir: Path, name: str) -> List[str]:
    d = raw_dir / name
    if not d.is_dir():
        return []
    return sorted(
        p.name for p in d.rglob("*")
        if p.is_file()
        and p.suffix.lower() in config.SUPPORTED_EXT
        and not p.name.startswith("~$")
    )


def _load_overrides() -> Dict[str, dict]:
    if not config.ASSISTANTS_FILE.exists():
        return {}
    try:
        data = json.loads(config.ASSISTANTS_FILE.read_text(encoding="utf-8"))
        return data.get("assistants", data) if isinstance(data, dict) else {}
    except Exception as e:
        print(f"[assistants] 配置读取失败，使用自动生成：{e}")
        return {}


def discover(raw_dir: Path = None) -> Dict[str, Assistant]:
    """扫描 data/raw，构建助手注册表。"""
    raw_dir = raw_dir or config.RAW_DIR
    if not raw_dir.exists():
        return {}

    overrides = _load_overrides()
    result: Dict[str, Assistant] = {}

    # 根目录下直接放的文档归入「通用」助手。
    # 注意：这一步必须在子目录循环**之外** —— 它扫的是 raw 根目录本身，
    # 与循环变量无关。原先写在循环里，一旦 data/raw 全空（没有任何子目录），
    # 循环体一次都不执行，下面的 root_docs 就成了未定义变量，直接 UnboundLocalError。
    root_docs = [p.name for p in raw_dir.iterdir()
                 if p.is_file() and p.suffix.lower() in config.SUPPORTED_EXT
                 and not p.name.startswith("~$")]

    dirs = sorted(p for p in raw_dir.iterdir() if p.is_dir())
    for d in dirs:
        name = d.name
        docs = _scan_docs(raw_dir, name)

        if not docs:
            continue

        cfg = overrides.get(name, {})
        display = cfg.get("display_name") or f"{name}助手"
        scope = cfg.get("scope") or AUTO_SCOPE_TEMPLATE.format(name=name)
        # 描述优先用配置；否则用文件名生成（文件名本身区分度很高）
        desc = cfg.get("description") or AUTO_DESC_TEMPLATE.format(
            name=name, docs="、".join(p.rsplit(".", 1)[0] for p in docs[:35])
        )

        result[name] = Assistant(
            name=name,
            display_name=display,
            scope=scope,
            description=desc,
            docs=docs,
            samples=cfg.get("samples") or [],
            extra_rules=cfg.get("extra_rules") or "",
            chunk_count=cfg.get("chunk_count", 0),
        )

    # 若有直接放在 raw 根目录的文档，单独归入「通用」
    if root_docs and "通用" not in result:
        result["通用"] = Assistant(
            name="通用",
            display_name="综合助手",
            scope="负责解答未归入特定分类的校园通用问题。",
            description="综合：" + "、".join(p.rsplit(".", 1)[0] for p in root_docs[:30]),
            docs=root_docs,
        )
    return result


def list_names(raw_dir: Path = None) -> List[str]:
    return list(discover(raw_dir).keys())


if __name__ == "__main__":
    reg = discover()
    print(f"发现 {len(reg)} 个小助手：\n")
    for name, a in reg.items():
        print(f"  [{name}] {a.display_name}  ({len(a.docs)} 个文档)")
        print(f"      职责：{a.scope}")
        print(f"      路由文本：{a.routing_text[:110]}...")
        print()
