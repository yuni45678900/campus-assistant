# -*- coding: utf-8 -*-
"""
命令行工具，用于建索引、调试检索、环境自检。

    python -m app.cli doctor          环境体检（先跑这个）
    python -m app.cli build           构建索引
    python -m app.cli search "问题"    只看检索结果，不调用大模型
    python -m app.cli ask "问题"       完整问答（终端流式输出）
    python -m app.cli chat            交互式问答
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time

from app import config

BANNER = r"""
  校园智能助手 · Campus Assistant
  Qwen3-8B (本地) + 混合检索 RAG
"""


def cmd_doctor(_args):
    """环境体检：依赖 → 文档 → 索引 → Ollama 逐层检查。"""
    print(BANNER)
    print(config.describe())
    ok = True

    print("\n[1/4] 检查 Python 依赖")
    deps = [
        ("fastapi", "Web 服务"), ("uvicorn", "ASGI 服务器"),
        ("httpx", "调用 Ollama"), ("numpy", "向量计算"),
        ("docx", "解析 Word 文档"), ("jieba", "中文分词"),
        ("rank_bm25", "关键词检索"), ("sentence_transformers", "向量与重排模型"),
    ]
    for mod, desc in deps:
        try:
            __import__(mod)
            print(f"   [OK] {mod:<22} {desc}")
        except ImportError:
            print(f"   [!!] {mod:<22} {desc}  ← 缺失，请执行 pip install -r requirements.txt")
            ok = False

    print("\n[2/4] 检查校园文档")
    if not config.RAW_DIR.exists():
        print(f"   [!!] 目录不存在：{config.RAW_DIR}")
        ok = False
    else:
        files = [p for p in config.RAW_DIR.rglob("*")
                 if p.is_file() and p.suffix.lower() in config.SUPPORTED_EXT
                 and not p.name.startswith("~$")]
        if files:
            for f in files:
                print(f"   [OK] {f.relative_to(config.RAW_DIR)}"
                      f"  ({f.stat().st_size / 1024:.0f} KB)")
        else:
            print(f"   [!!] {config.RAW_DIR} 下还没有文档")
            print(f"        请把学校简介 / 专业介绍 / 食堂介绍等资料放进去")
            ok = False

    print("\n[3/4] 检查索引（按助手分库）")
    from app.indexer import index_status
    st = index_status()
    if st.get("ready"):
        print(f"   [OK] {st['assistant_count']} 个小助手，共 {st['chunk_count']} 个片段，"
              f"构建于 {st.get('built_at')}")
        for a in st.get("assistants", []):
            print(f"        · {a['name']:<8} {a['doc_count']:>2} 个文档 / "
                  f"{a['chunk_count']:>3} 个片段")
    else:
        print("   [--] 索引尚未构建，请执行：python -m app.cli build")
        ok = False

    print("\n[4/4] 检查 Ollama 与模型")
    from app.llm import client
    try:
        h = asyncio.run(client.health())
    except Exception as e:
        h = {"message": str(e), "running": False, "model_ready": False}
    if h.get("running"):
        print(f"   [OK] Ollama 正在运行，已有模型：{', '.join(h.get('models') or ['无'])}")
        if h.get("model_ready"):
            print(f"   [OK] 目标模型 {config.LLM_MODEL} 已就绪")
        else:
            print(f"   [!!] {h.get('message')}")
            print(f"        执行：ollama pull {config.LLM_MODEL}")
            ok = False
    else:
        print(f"   [!!] {h.get('message')}")
        print("        安装 Ollama：https://ollama.com/download")
        print("        启动服务：  ollama serve")
        ok = False

    print("\n" + "=" * 52)
    print("  体检结论：" + ("全部通过，可以启动服务 ✔" if ok
                           else "存在未就绪项，请按上面提示处理后重试"))
    print("=" * 52)
    return 0 if ok else 1


def cmd_build(args):
    from app.indexer import build_index
    print(BANNER)
    try:
        manifest = build_index(verbose=True)
    except Exception as e:
        print(f"\n构建失败：{e}")
        return 1
    n = len(manifest.get("assistants", {}))
    print(f"\n完成：{n} 个小助手，共 {manifest.get('total_chunks', 0)} 个片段，"
          f"总耗时 {manifest.get('total_sec')}s")
    return 0


def cmd_search(args):
    from app.rag import engine
    if not engine.ready:
        print("索引不可用，请先执行：python -m app.cli build")
        return 1
    t0 = time.time()
    hits = engine.retrieve(args.question, [])
    print(f"\n检索耗时 {time.time() - t0:.3f}s，命中 {len(hits)} 条\n")
    for i, h in enumerate(hits, 1):
        print(f"[{i}] 来源《{h.chunk.source}》{(' — ' + h.chunk.section) if h.chunk.section else ''}")
        print(f"    最终分 {h.score:.4f} | 向量名次 {h.vector_rank} | "
              f"BM25 名次 {h.bm25_rank} | 重排分 "
              f"{'—' if h.rerank_score is None else round(h.rerank_score, 4)}")
        print(f"    {h.chunk.text[:200].replace(chr(10), ' ')}...\n")
    return 0


def _print_events(question, history):
    from app.rag import engine
    got = False
    print("\n助手：", end="", flush=True)

    async def run():
        nonlocal got
        async for evt in engine.answer_stream(question, history):
            t = evt["type"]
            if t == "status":
                print(f"\n  · {evt['data']}", flush=True)
            elif t == "sources":
                srcs = evt["data"]
                if srcs:
                    print("  · 命中资料：" + " / ".join(
                        f"{s['source']}" + (f"[{s['section']}]" if s["section"] else "")
                        for s in srcs), flush=True)
                print("\n助手：", end="", flush=True)
            elif t == "delta":
                got = True
                print(evt["data"], end="", flush=True)
            elif t == "done":
                print(f"\n\n（耗时 {evt['data'].get('elapsed')}s）", flush=True)
            elif t == "error":
                print(f"\n[错误] {evt['data']}", flush=True)

    asyncio.run(run())
    return got


def cmd_ask(args):
    from app.rag import engine
    if not engine.ready:
        print("索引不可用，请先执行：python -m app.cli build")
        return 1
    _print_events(args.question, [])
    return 0


def cmd_chat(_args):
    from app.rag import engine
    if not engine.ready:
        print("索引不可用，请先执行：python -m app.cli build")
        return 1
    print(BANNER)
    print("  输入问题开始对话，输入 exit / quit 退出\n")
    history = []
    while True:
        try:
            q = input("你：").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not q:
            continue
        if q.lower() in ("exit", "quit", "q"):
            break
        _print_events(q, history)
        history.append({"role": "user", "content": q})
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="校园智能助手命令行工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("doctor", help="环境体检")
    sub.add_parser("build", help="构建/重建索引")

    p_search = sub.add_parser("search", help="只做检索（不调用大模型）")
    p_search.add_argument("question")

    p_ask = sub.add_parser("ask", help="单次问答")
    p_ask.add_argument("question")

    sub.add_parser("chat", help="交互式问答")

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return 0

    return {
        "doctor": cmd_doctor,
        "build": cmd_build,
        "search": cmd_search,
        "ask": cmd_ask,
        "chat": cmd_chat,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main() or 0)
