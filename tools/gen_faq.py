# -*- coding: utf-8 -*-
"""
生成学生端左侧「常见问题」菜单。

问题不是手写的，而是**从知识库里长出来**：按文档切主题 → 让模型出题 →
用真实检索校验「这道题能在它自己的资料上检索到」。校验不过的直接丢弃，
保证菜单里每一道题点下去都能答得出来。

用法（项目根目录下）：
    .venv\\Scripts\\python.exe tools\\gen_faq.py             # 全部助手
    .venv\\Scripts\\python.exe tools\\gen_faq.py 招生 迎新    # 只做指定助手
    .venv\\Scripts\\python.exe tools\\gen_faq.py --no-llm    # 不调模型，仅用文档名兜底出题

产出：data/faq.json —— 服务端 /api/faq 读它，学生端左侧菜单渲染它。
改完资料重建索引后重跑本脚本即可。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import faq  # noqa: E402


def main() -> int:
    args = [a for a in sys.argv[1:]]
    use_llm = "--no-llm" not in args
    names = [a for a in args if not a.startswith("--")] or None

    print("=" * 62)
    print("常见问题生成器")
    print("  出题：", "本地模型" if use_llm else "文档名兜底（不调模型）")
    print("  范围：", "、".join(names) if names else "全部已建索引的助手")
    print("=" * 62)

    payload = faq.generate(assistants=names, use_llm=use_llm)

    print()
    print("─" * 62)
    for name, a in (payload.get("assistants") or {}).items():
        print(f"\n【{name}】通过 {a.get('verified', 0)} 道 / 丢弃 "
              f"{a.get('dropped', 0)} 道 · {len(a.get('groups', []))} 个分组")
        for g in a.get("groups", []):
            print(f"  ▾ {g['c']}")
            for it in g["items"]:
                print(f"      · {it['t']}   ← {it['src']}")
    print()
    print("已写入：", ROOT / "data" / "faq.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
