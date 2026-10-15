# -*- coding: utf-8 -*-
"""
模拟 Ollama 服务，用于在**尚未安装 Ollama / 模型还没下载完成**时，
先验证整条链路的连通性（检索 → 流式 SSE → 前端渲染）。

它不产生任何真实智能，只是把检索到的资料拼成一段占位回答。

    # 终端 A
    python tools/mock_ollama.py

    # 终端 B（把请求指向模拟服务）
    set OLLAMA_HOST=http://127.0.0.1:11435
    python -m app.server
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

app = FastAPI(title="Mock Ollama")

FAKE_ANSWER = """（模拟回答）这是一条占位回复，用于验证系统链路是否打通。

从检索到的资料来看，你问的内容在资料库中确实存在对应条目，真实回答将由
Ollama 中的 Qwen3-8B 生成。当前显示这段文字，说明以下环节都工作正常：

1. 文档解析与切片没有问题；
2. 向量检索与关键词检索都能返回结果；
3. SSE 流式传输正常；
4. 前端能够逐字渲染并展示来源卡片。

接入真实模型只需安装 Ollama 并执行 ollama pull qwen3:8b 即可。"""

_CTX_RE = re.compile(r"【参考资料】\n(.*?)\n\n【问题】", re.S)


@app.get("/api/tags")
async def tags():
    return {"models": [{"name": "qwen3:8b", "size": 5200000000}]}


@app.post("/api/chat")
async def chat(req: dict):
    messages = req.get("messages") or []
    user_msg = next((m.get("content", "") for m in reversed(messages)
                     if m.get("role") == "user"), "")

    m = _CTX_RE.search(user_msg)
    ctx = m.group(1) if m else ""
    hit_count = len(re.findall(r"^\[\d+\] 来源", ctx, re.M))

    head = f"（模拟回答 · 检索到 {hit_count} 个资料片段）\n\n"
    body = head + FAKE_ANSWER + "\n\n【命中的资料片段】\n" + (
        ctx[:400] if ctx and ctx != "（无）" else "无"
    )

    async def gen():
        # 逐字输出，模拟真实的流式效果
        for ch in body:
            chunk = {"message": {"role": "assistant", "content": ch},
                     "done": False}
            yield json.dumps(chunk, ensure_ascii=False) + "\n"
            await asyncio.sleep(0.012)
        yield json.dumps({"message": {"role": "assistant", "content": ""},
                          "done": True}, ensure_ascii=False) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


if __name__ == "__main__":
    import uvicorn
    print("模拟 Ollama 已启动 → http://127.0.0.1:11435")
    print("另开终端设置 OLLAMA_HOST=http://127.0.0.1:11435 后再启动 app.server")
    uvicorn.run(app, host="127.0.0.1", port=11435, log_level="warning")
