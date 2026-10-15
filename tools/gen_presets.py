# -*- coding: utf-8 -*-
"""生成在线展示版所需的预置问答（真实调用本机 RAG 链路，不是编的）。

用法：
    .venv\\Scripts\\python.exe tools/gen_presets.py            # 全部生成（跳过已完成的）
    .venv\\Scripts\\python.exe tools/gen_presets.py --only 答疑

产物：demo-site/qa.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000"
OUT = Path(__file__).resolve().parent.parent / "demo-site" / "qa.json"

PLAN = [
    ("伴学", [
        "绩点是怎么算的？怎么换算成 5.0 制？",
        "选课有什么要注意的？什么时候开始选？",
        "考试挂科了，补考、缓考、重修分别怎么走流程？",
        "第二课堂学分怎么获得？有什么要求？",
    ]),
    ("招生", [
        "2026 年的招生计划和招生章程有哪些要点？",
        "学校一共有哪些学院？分别开设什么专业？",
        "2026 年招生计划一共招多少人？",
        "学校的优势学科和特色专业是哪些？",
    ]),
    ("答疑", [
        "学校有哪些社团？",
        "快递地址怎么填？到哪里取快递？",
        "学校食堂有什么好吃的推荐？",
        "宿舍里哪些电器属于违规电器？",
    ]),
    ("迎新", [
        "新生报到流程是怎样的？报到需要带哪些材料？",
        "学费和住宿费怎么缴纳？有哪些缴费方式？",
        "新生体检是怎么安排的？",
        "学生宿舍是几人间？入住要注意什么？",
    ]),
]

AUTO_PLAN = [
    "学校有哪些社团？",
    "新生报到需要带什么材料？",
    "2026 年招生章程的要点是什么？",
]


def ask(client: httpx.Client, question: str, assistant: str = "auto") -> dict:
    """调用 /api/chat，收集 SSE，返回 {answer, sources, elapsed, chars, route}。"""
    payload = {"question": question, "history": [], "assistant": assistant}
    deltas: list[str] = []
    meta: dict = {}
    with client.stream("POST", f"{BASE}/api/chat", json=payload, timeout=600) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            try:
                evt = json.loads(line[6:])
            except Exception:
                continue
            t = evt.get("type")
            if t == "delta":
                deltas.append(evt.get("data", ""))
            elif t in ("route", "sources", "done", "error"):
                meta[t] = evt.get("data")
    answer = "".join(deltas).strip()
    done = meta.get("done") or {}
    return {
        "q": question,
        "a": answer,
        "sources": meta.get("sources") or [],
        "elapsed": done.get("elapsed"),
        "chars": len(answer),
        "route": meta.get("route") or {},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None, help="只生成指定助手")
    args = ap.parse_args()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if OUT.exists():
        try:
            data = json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            data = {}

    # trust_env=False：必须，否则会话代理会把 127.0.0.1 也代理走
    with httpx.Client(trust_env=False) as client:
        for name, questions in PLAN:
            if args.only and args.only != name:
                continue
            bucket = data.setdefault(name, [])
            have = {x["q"] for x in bucket}
            for q in questions:
                if q in have:
                    print(f"[skip] {name} · {q}", flush=True)
                    continue
                t0 = time.time()
                print(f"[run ] {name} · {q}", flush=True)
                try:
                    item = ask(client, q, assistant=name)
                except Exception as e:  # noqa: BLE001
                    print(f"  !! 失败：{type(e).__name__}: {e}", flush=True)
                    continue
                bucket.append(item)
                data[name] = bucket
                OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                               encoding="utf-8")
                print(f"  ok  {item['chars']} 字 / {item['elapsed']}s / "
                      f"{len(item['sources'])} 来源 / 耗时 {time.time()-t0:.0f}s", flush=True)

        # 自动路由演示
        auto = data.setdefault("__auto__", [])
        have = {x["q"] for x in auto}
        for q in AUTO_PLAN:
            if q in have:
                continue
            try:
                auto.append(ask(client, q, assistant="auto"))
            except Exception as e:  # noqa: BLE001
                print(f"  !! auto 失败：{e}", flush=True)
            data["__auto__"] = auto
            OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"[auto] {q}", flush=True)

    print(f"\n完成，输出：{OUT}", flush=True)
    print(f"总条数：{sum(len(v) for k, v in data.items())}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
