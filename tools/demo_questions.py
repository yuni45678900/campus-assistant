# -*- coding: utf-8 -*-
"""预演演示用的三个问题，确认现场答得出 / 答不出是符合预期的。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                       # noqa: BLE001
    pass

import httpx                                            # noqa: E402

BASE = "http://127.0.0.1:8000"
QUESTIONS = [
    ("① 能答的    ", "新生报到需要带哪些材料？"),
    ("② 答不了的  ", "校内停车场怎么收费？"),
    ("③ 跨分类的  ", "大一新生什么时候报到？到了之后怎么选课？"),
]


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=300.0, trust_env=False)
    r = c.post("/api/auth/login", json={"username": "admin", "password": "admin11",
                                        "target": "admin"})
    print("登录:", r.status_code)
    print("索引:", c.get("/api/status").json()["index"].get("assistant_count"),
          "分类 /", c.get("/api/status").json()["index"].get("chunk_count"), "片段")
    print("=" * 64)

    for tag, q in QUESTIONS:
        text, route, sources = "", None, 0
        try:
            with c.stream("POST", "/api/chat",
                          json={"question": q, "history": []}) as resp:
                for line in resp.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        evt = json.loads(line[5:].strip())
                    except Exception:                   # noqa: BLE001
                        continue
                    t = evt.get("type")
                    if t == "delta":
                        text += evt.get("data") or ""
                    elif t == "route":
                        route = evt.get("data")
                    elif t == "sources":
                        sources = len(evt.get("data") or [])
                    elif t == "error":
                        text += f"\n[ERROR] {evt.get('data')}"
        except Exception as e:                          # noqa: BLE001
            text = f"[请求失败] {e}"

        print(f"{tag} 问：{q}")
        print(f"   路由：{route}   引用：{sources} 份   回答 {len(text)} 字")
        print("   答：" + text.strip().replace("\n", "\n       ")[:420])
        print("-" * 64)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
