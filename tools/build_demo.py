# -*- coding: utf-8 -*-
"""构建「在线展示版」站点：抓本机快照 -> 内联成 data.js -> 派生四个助手页。

用法：
    .venv\\Scripts\\python.exe tools/build_demo.py

产物：demo-site/assets/data.js + demo-site/{banxue,zhaosheng,dayi,yingxin}.html
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000"
ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "demo-site"

# 助手名 -> 派生页面文件名 / 页面标题后缀
PAGES = [
    ("伴学", "banxue.html", "伴学助手 · 学习搭子"),
    ("招生", "zhaosheng.html", "招生助手 · 报考向导"),
    ("答疑", "dayi.html", "答疑助手 · 校园百科"),
    ("迎新", "yingxin.html", "迎新助手 · 新生向导"),
]


def fetch(client: httpx.Client, path: str) -> dict:
    r = client.get(f"{BASE}{path}", timeout=30)
    r.raise_for_status()
    return r.json()


def main() -> int:
    qa_file = SITE / "qa.json"
    if not qa_file.exists():
        print("!! 缺少 demo-site/qa.json，请先跑 tools/gen_presets.py")
        return 1
    qa = json.loads(qa_file.read_text(encoding="utf-8"))

    # trust_env=False：会话代理会把 127.0.0.1 也代理走
    with httpx.Client(trust_env=False) as c:
        overview = fetch(c, "/api/admin/overview")
        status = fetch(c, "/api/status")
        trash = fetch(c, "/api/admin/trash")

    cfg = status.get("config", {})
    assistants = []
    for a in overview.get("assistants", []):
        assistants.append({
            "name": a["name"],
            "display_name": a.get("display_name") or a["name"],
            "scope": a.get("scope") or "",
            "doc_count": a.get("doc_count", 0),
            "chunk_count": a.get("chunk_count", 0),
            "indexed": bool(a.get("indexed")),
            "built_at": a.get("built_at") or "",
            "samples": a.get("samples") or [],
        })

    site = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "model": cfg.get("model", "qwen3:8b"),
        "embed_model": status.get("index", {}).get("embed_model", "BAAI/bge-small-zh-v1.5"),
        "rerank_model": cfg.get("rerank_model", "BAAI/bge-reranker-base"),
        "retrieval": cfg.get("retrieval") or "向量 Top8 + BM25 Top8 → RRF → 重排 Top3",
        "chunk": cfg.get("chunk") or "350 字 / 重叠 50 字",
        "overview": overview,
        "status": status,
        "trash": trash.get("batches", []),
        "assistants": assistants,
        "qa": {k: v for k, v in qa.items() if not k.startswith("__")},
    }

    # 1) data.js
    out = SITE / "assets" / "data.js"
    out.parent.mkdir(parents=True, exist_ok=True)
    body = "/* 构建时自动生成，请勿手改。来源：本机 RAG 链路真实问答 + /api 快照。*/\n"
    body += "window.SITE = " + json.dumps(site, ensure_ascii=False, indent=1) + ";\n"
    out.write_text(body, encoding="utf-8")
    print(f"✓ 写入 {out}（{out.stat().st_size/1024:.0f} KB）")

    stats = {a["name"]: (a["doc_count"], a["chunk_count"]) for a in assistants}
    print("  助手快照：" + "，".join(f"{k} {v[0]}文档/{v[1]}片段" for k, v in stats.items()))
    print("  预置问答：" + "，".join(f"{k} {len(v)} 条" for k, v in site["qa"].items()))

    # 2) 派生四个助手页
    tpl = (SITE / "student.html").read_text(encoding="utf-8")
    if 'assistant: ""' not in tpl:
        print("!! student.html 缺少可替换的 PAGE 行")
        return 1
    for name, fname, title in PAGES:
        html = tpl.replace('assistant: ""', f'assistant: "{name}"')
        html = re.sub(r"<title>[^<]*</title>", f"<title>校园小助手 · {title}</title>", html, count=1)
        (SITE / fname).write_text(html, encoding="utf-8")
        print(f"✓ 派生 {fname}")

    print("\n完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
