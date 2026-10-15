# -*- coding: utf-8 -*-
"""
演示前自检：把 data/faq.json 里菜单上的每一道题都真问一遍。

菜单是自动生成的，出题时已做过"检索必须命中自己的资料"的校验；这个脚本
再做一层端到端确认——**真的打问答接口，看回答有没有落成"现有资料未包含该信息"**。
演示前跑一遍，心里有底。

用法（项目根目录下，服务需已在跑）：
    .venv\\Scripts\\python.exe tools\\verify_faq.py
    .venv\\Scripts\\python.exe tools\\verify_faq.py --base http://127.0.0.1:8011
    .venv\\Scripts\\python.exe tools\\verify_faq.py 招生 迎新     # 只查指定助手
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from app import config  # noqa: E402

MISS_PATTERNS = ("未包含该信息", "没有找到相关内容", "资料中没有", "无法回答")


def ask(base: str, cookies: dict, assistant: str, question: str,
        timeout: float = 180.0) -> str:
    """打一次 /api/chat（SSE），把正文拼起来。

    事件格式：{"type":"delta","data":"正文片段"}；另有 status / route /
    sources / done 几类，只有 delta 是回答正文。
    """
    text = ""
    payload = {"question": question, "history": [], "assistant": assistant}
    with httpx.Client(cookies=cookies, timeout=timeout) as c:
        with c.stream("POST", base + "/api/chat", json=payload) as r:
            if r.status_code >= 400:
                return f"[HTTP {r.status_code}]"
            for line in r.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                try:
                    ev = json.loads(line[6:])
                except Exception:
                    continue
                if ev.get("type") == "delta":
                    v = ev.get("data")
                    if isinstance(v, str):
                        text += v
    return text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("assistants", nargs="*", help="只查这些助手（默认全部）")
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    args = ap.parse_args()

    faq_file = config.DATA_DIR / "faq.json"
    if not faq_file.exists():
        print(f"找不到 {faq_file}，先跑 tools/gen_faq.py")
        return 1
    data = json.loads(faq_file.read_text(encoding="utf-8"))

    base = args.base.rstrip("/")
    with httpx.Client(timeout=30) as c:
        r = c.post(base + "/api/auth/login", json={
            "username": config.STUDENT_USER, "password": config.STUDENT_PASS,
            "target": "student"})
        if r.status_code >= 400:
            print(f"登录失败：HTTP {r.status_code} {r.text[:200]}")
            return 1
        cookies = dict(r.cookies)

    print("=" * 66)
    print(f"逐题自检 · {base}")
    print("=" * 66)

    total = ok = 0
    bad: list = []
    for name, a in (data.get("assistants") or {}).items():
        if args.assistants and name not in args.assistants:
            continue
        for g in a.get("groups", []):
            for it in g.get("items", []):
                q = it["t"]
                total += 1
                try:
                    ans = ask(base, cookies, name, q)
                except Exception as e:
                    ans = f"[异常 {e}]"
                good = (len(ans.strip()) > 15
                        and not any(p in ans for p in MISS_PATTERNS)
                        and not ans.startswith("[HTTP")
                        and not ans.startswith("[异常"))
                if good:
                    ok += 1
                    print(f"  ✔ [{name}] {q}")
                else:
                    bad.append((name, g["c"], q, ans.replace("\n", " ")[:90]))
                    print(f"  ✘ [{name}] {q}  →  {ans.replace(chr(10), ' ')[:70]}")

    print()
    print("=" * 66)
    print(f"合计 {total} 道，答得出 {ok} 道，异常 {len(bad)} 道")
    if bad:
        print("\n需要处理的：")
        for name, cat, q, ans in bad:
            print(f"  [{name} / {cat}] {q}\n      {ans}")
    print("=" * 66)
    return 0 if not bad else 2


if __name__ == "__main__":
    raise SystemExit(main())
