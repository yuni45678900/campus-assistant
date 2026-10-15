# -*- coding: utf-8 -*-
"""
端到端验收脚本：用真实问题检验「检索 + 生成」的整体质量。

    # 用内置示例问题（针对 data/raw/sample/ 的演示文档）
    python tools/verify_e2e.py

    # 自定义问题，格式 "问题|必须出现的关键词"，可写多个
    python tools/verify_e2e.py "奖学金怎么申请|奖学金" "宿舍几点断电|23:30"

关键词要挑**一定不会变**的短片段（数字、专有名词）。
不要写整句或带标点的长串——模型换个措辞（"13:00—13:40" 写成 "13:00至13:40"）
就会误报未命中。

需要服务已经在运行（start.bat）。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000"

# 内置示例问题：对应 tools/make_sample_docs.py 生成的演示文档
DEFAULT_CASES = [
    ("第三食堂周五中午有什么特殊安排？", "13:40"),
    ("计算机科学与技术专业要修多少学分？", "165"),
    ("学校的校庆日是哪一天？", "10月18日"),
    ("人工智能专业是哪一年开始招生的？", "2022"),
    ("图书馆自习区几点关门？", "23:00"),
    ("校园卡丢失了怎么补办？", "20元"),
]


def ask(question: str) -> tuple:
    """返回 (回答, 来源片段数, 首字延迟, 总耗时)。"""
    t0 = time.time()
    answer, first, sources = "", None, []
    try:
        with httpx.stream("POST", f"{BASE}/api/chat",
                          json={"question": question, "history": []},
                          timeout=300) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    evt = json.loads(line[5:])
                except json.JSONDecodeError:
                    continue
                t = evt.get("type")
                if t == "delta":
                    if first is None:
                        first = time.time() - t0
                    answer += evt["data"]
                elif t == "sources":
                    sources = evt["data"]
                elif t == "error":
                    return f"[错误] {evt['data']}", 0, None, time.time() - t0
    except httpx.ConnectError:
        print("无法连接服务，请先运行 start.bat")
        sys.exit(1)
    return answer, len(sources), first, time.time() - t0


def main() -> int:
    cases = []
    for arg in sys.argv[1:]:
        if "|" in arg:
            q, kw = arg.split("|", 1)
            cases.append((q.strip(), kw.strip()))
    if not cases:
        cases = DEFAULT_CASES
        print("（未指定问题，使用内置示例问题）\n")

    print(f"{'问题':<34} {'结果':<6} {'首字':>7} {'总耗时':>8}  {'引用':>4}")
    print("-" * 74)

    passed = 0
    for q, keyword in cases:
        answer, n_src, first, elapsed = ask(q)
        ok = keyword in answer
        passed += ok
        label = "✓ 命中" if ok else "✗ 未中"
        first_s = f"{first:.1f}s" if first else "—"
        print(f"{q[:32]:<34} {label:<6} {first_s:>7} {elapsed:>7.1f}s  {n_src:>4}")
        if not ok:
            print(f"    预期关键词「{keyword}」未出现，实际回答：")
            print(f"    {answer.strip()[:220]}")

    total = len(cases)
    print("-" * 74)
    print(f"命中 {passed}/{total}  ({passed / total * 100:.0f}%)")
    if passed < total:
        print("\n未命中的排查顺序：")
        print("  1. python -m app.cli search \"该问题\"   看检索是否召回了正确片段")
        print("  2. 检索没问题 → 是生成环节，检查资料里是否真有该信息")
        print("  3. 检索就没召回 → 文档可能没解析成功，跑 python -m app.cli doctor")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
