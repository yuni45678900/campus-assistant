# -*- coding: utf-8 -*-
"""
FastAPI 服务层：把多助手 RAG 链路暴露成 SSE 流式接口，并托管前端页面。

启动：
    python -m app.server               # 或
    uvicorn app.server:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import asyncio
import hmac
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from typing import List, Optional

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (FileResponse, JSONResponse, RedirectResponse,
                               Response, StreamingResponse)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import (accounts, admin, assistants, auth, classifier, config, faq,
                 records, site)
from app.indexer import build_index, index_status, store
from app.llm import client as ollama
from app.rag import engine


def _warmup_sync() -> None:
    """
    预热：把"首次加载"的代价从用户第一次提问挪到服务启动阶段。

    实测冷启动约 60 秒（加载 1.2 GB 权重 + torch 首次推理初始化 + 文件缓存预热），
    预热完成后单次检索只要 1 秒左右。这一步不做，用户的第一个问题会等到怀疑人生。
    """
    from app.retriever import reranker

    names = store.built_names()
    if not names:
        print("  [预热] 索引未就绪，跳过（请先执行 build.bat）")
        return

    t0 = time.time()
    store.all()
    print(f"  [预热] {len(names)} 个助手索引已载入（{'、'.join(names)}）"
          f"  {time.time() - t0:.1f}s", flush=True)

    t1 = time.time()
    engine.registry(force=True)
    print(f"  [预热] 助手注册表就绪  {time.time() - t1:.1f}s", flush=True)

    if config.ENABLE_RERANK:
        t2 = time.time()
        reranker.warmup()
        print(f"  [预热] 重排模型就绪  {time.time() - t2:.1f}s", flush=True)

    # 触发编码器与路由的首次计算（torch/oneDNN 算子初始化）
    t3 = time.time()
    engine.registry() and None
    print(f"  [预热] 路由链路就绪  {time.time() - t3:.1f}s", flush=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    print("\n  正在预热检索链路（首次约需 30~60 秒，请稍候）...", flush=True)
    try:
        await asyncio.to_thread(_warmup_sync)
    except Exception as e:
        print(f"  [预热] 失败：{e}", flush=True)
    print("  预热完成，服务已就绪。\n", flush=True)
    yield


app = FastAPI(title="校园多助手智能问答系统", version="2.0.0", lifespan=lifespan)

# 三个页面（门户 / 学生端 / 管理端）一律"每次向服务端校验"，别让浏览器自作主张拿旧版。
# 不加这个头时 FastAPI 只回 Last-Modified + ETag，浏览器会走启发式缓存 ——
# 表现就是：改完页面刷新还是老样子，甚至两个标签页样式不一样（一个缓存了一个没有）。
_NO_STORE = {"Cache-Control": "no-cache, must-revalidate"}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/web", StaticFiles(directory=str(config.WEB_DIR)), name="web")
# 文档内嵌图片的静态服务：构建索引时会把图片解压到 data/assets/ 下
app.mount("/assets", StaticFiles(directory=str(config.ASSETS_DIR)), name="assets")


# --------------------------------------------------------------------------
# 请求模型
# --------------------------------------------------------------------------
class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    history: List[ChatMessage] = Field(default_factory=list)
    assistant: str = Field("auto", description="auto 表示自动路由，也可指定助手名")


class ReindexRequest(BaseModel):
    only: Optional[List[str]] = Field(None, description="只重建指定助手，留空则全量")


# --------------------------------------------------------------------------
# 页面
# --------------------------------------------------------------------------
@app.get("/")
async def index():
    return FileResponse(str(config.WEB_DIR / "index.html"),
                        headers=_NO_STORE)


@app.get("/favicon.ico")
async def favicon():
    # 注意：204 响应不能携带响应体。若返回 JSONResponse({}) 会写入 2 字节 body，
    # h11 会抛 "Too much data for declared Content-Length"，浏览器一访问就报错。
    return Response(status_code=204)


# --------------------------------------------------------------------------
# 状态与助手列表
# --------------------------------------------------------------------------
@app.get("/api/status")
async def status():
    st = await asyncio.to_thread(index_status)
    llm = await ollama.health()
    return {
        "index": st,
        "llm": llm,
        "config": {
            "model": config.LLM_MODEL,
            "embed_model": config.EMBED_MODEL,
            "rerank": config.ENABLE_RERANK,
            "top_n": config.RERANK_TOP_N,
            "chunk_size": config.CHUNK_SIZE,
            "route_abs": config.ROUTE_ABS_THRESHOLD,
            "route_margin": config.ROUTE_MARGIN,
        },
    }


@app.get("/api/assistants")
async def assistants_api(request: Request):
    """小助手清单（含索引状态），供前端渲染助手选择器。需已登录。"""
    if not _student_authed(request):
        return JSONResponse({"ok": False, "error": "请先登录"}, status_code=401)
    reg = await asyncio.to_thread(engine.registry)
    st = await asyncio.to_thread(index_status)
    counts = {a["name"]: a for a in st.get("assistants", [])}

    out = []
    for name, a in reg.items():
        c = counts.get(name, {})
        out.append({
            "name": name,
            "display_name": a.display_name,
            "scope": a.scope,
            "doc_count": c.get("doc_count", len(a.docs)),
            "chunk_count": c.get("chunk_count", 0),
            "samples": a.samples,
            "indexed": name in counts,
        })
    return {"assistants": out,
            "total_chunks": st.get("chunk_count", 0),
            "total_docs": sum(x["doc_count"] for x in out)}


@app.get("/api/faq")
async def faq_api(request: Request):
    """学生端左侧「常见问题」菜单。

    内容不是手写的，而是由 tools/gen_faq.py 从知识库自动生成：
    按文档切主题 → 让模型出题 → **逐题用真实检索校验"这道题能在它自己的资料上
    检索到"**，不通过的直接丢弃，所以菜单里每一道题点下去都答得出来。
    产出在 data/faq.json，这里只负责读出来。
    """
    if not _student_authed(request):
        return JSONResponse({"ok": False, "error": "请先登录"}, status_code=401)
    data = await asyncio.to_thread(faq.load)
    return {"assistants": data.get("assistants") or {},
            "built_at": data.get("built_at")}


# --------------------------------------------------------------------------
# 问答（SSE 流式）
# --------------------------------------------------------------------------
def _used_assistant(requested: str, done: Optional[dict]) -> str:
    """这一轮实际用的是哪个助手。

    请求里写 auto 时只是"让系统自己判断"，真正落到哪个知识库要看路由结果。
    记录里存真实那个才有意义 —— 后台一眼能看出哪类问题被分给了谁。
    """
    route = ((done or {}).get("trace") or {}).get("route") or {}
    sel = route.get("selected") or []
    if len(sel) == 1:
        return str(sel[0])
    if requested and requested != "auto":
        return str(requested)
    return "自动路由"


@app.post("/api/chat")
async def chat(request: Request, req: ChatRequest):
    """问答主链路（SSE）。需已登录 —— 学生端和门户之外拿到这个接口也没用。"""
    if not _student_authed(request):
        return JSONResponse({"ok": False, "error": "登录已失效，请重新登录"},
                            status_code=401)
    history = [m.model_dump() for m in req.history]

    # 提问的人：记录里要落"谁问的"，管理端那页才能按学校 / 账号看
    me = _identity(request)
    who = me.as_dict() if me is not None else {}

    async def event_stream():
        parts: List[str] = []
        sources: List[dict] = []
        done: Optional[dict] = None
        interrupted = False
        try:
            async for evt in engine.answer_stream(req.question, history,
                                                  assistant=req.assistant):
                kind = evt.get("type")
                # 顺手把要记录的东西攒下来（不额外多跑一遍链路）
                if kind == "delta":
                    parts.append(str(evt.get("data") or ""))
                elif kind == "sources":
                    sources = evt.get("data") or []
                elif kind == "done":
                    done = evt.get("data") or {}
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:      # 用户关掉页面 → 停止生成
            interrupted = True
            raise
        except Exception as e:
            payload = {"type": "error", "data": f"服务异常：{e}"}
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        finally:
            # 后台记录：无论正常结束、报错还是中途关页面，都留一条。
            # 放在 finally 里且用同步写 —— 生成器被取消时 await 会直接抛错，
            # 而这里只是往一行 jsonl 追加，耗时微秒级，不拖慢事件循环。
            records.add({
                "user": who.get("username", ""),
                "name": who.get("name", ""),
                "school": who.get("school", ""),
                "role": who.get("role", ""),
                "assistant": _used_assistant(req.assistant, done),
                "question": req.question,
                "answer": "".join(parts),
                "elapsed": (done or {}).get("elapsed"),
                "chars": (done or {}).get("chars"),
                "no_hit": bool((done or {}).get("no_hit")),
                "interrupted": interrupted,
                "sources": [h.get("source") for h in sources if isinstance(h, dict)],
            })

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# --------------------------------------------------------------------------
# 检索调试（不调用大模型，快速看路由与检索质量）
# --------------------------------------------------------------------------
@app.post("/api/retrieve")
async def retrieve_only(request: Request, req: ChatRequest):
    """只检索不生成，用于排查"为什么没答对"。需已登录。"""
    if not _student_authed(request):
        return JSONResponse({"ok": False, "error": "请先登录"}, status_code=401)
    from app.assistants import discover
    from app.retriever import HybridRetriever
    from app.router import router

    t0 = time.time()
    reg = await asyncio.to_thread(discover)
    decision = await asyncio.to_thread(router.route, req.question, reg, req.assistant)
    indexes = await asyncio.to_thread(store.get_many, decision.selected)

    hits, rtrace = [], {}
    if indexes:
        def _run():
            r = HybridRetriever(indexes)
            t = {}
            h = r.search(req.question, trace=t)
            return h, t
        hits, rtrace = await asyncio.to_thread(_run)

    return {
        "elapsed": round(time.time() - t0, 3),
        "route": decision.to_dict(),
        "retrieve": rtrace,
        "count": len(hits),
        "hits": [h.to_dict() for h in hits],
    }


# --------------------------------------------------------------------------
# 重建索引
# --------------------------------------------------------------------------
@app.post("/api/reindex")
async def reindex(request: Request, req: ReindexRequest = None):
    """重建索引——它是写操作（会重算全库向量），所以和管理接口一个待遇：只认管理身份。"""
    bad = _guard(request)
    if bad:
        return bad
    try:
        only = req.only if req else None
        manifest = await asyncio.to_thread(build_index, only)
        engine.reload()
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    return {"ok": True, "manifest": manifest}


# ==========================================================================
# 学生端 / 管理端 —— 页面（都带门禁）
# ==========================================================================
# 权限是**包含**关系：管理账号既能进管理端，也能进学生端；
# 学生账号只能进学生端，手动敲 /admin 会被服务端打回门户。
# 未登录一律 302 到门户，门户按 ?locked=<角色> 弹出对应的登录框。
def _deny(role: str) -> RedirectResponse:
    return RedirectResponse(config.LOGIN_DENY_REDIRECT.format(role=role),
                            status_code=302)


@app.get("/student")
async def student_page(request: Request):
    if not _student_authed(request):
        return _deny("student")
    return FileResponse(str(config.WEB_DIR / "student.html"),
                        headers=_NO_STORE)


@app.get("/student/{name}")
async def student_page_named(request: Request, name: str):
    """
    单个助手的独立对话页。助手是**按 data/raw 动态发现**的，这里不硬编码，
    所以以后在管理端新建一个助手，直接访问 /student/<新助手名> 就能用。
    页面自己读 URL 里的助手名来换肤。
    """
    if not _student_authed(request):
        return _deny("student")
    return FileResponse(str(config.WEB_DIR / "student.html"),
                        headers=_NO_STORE)


@app.get("/admin")
async def admin_page(request: Request):
    """
    管理端页面——权限最紧的一扇门。

    只有管理账号（或带旁路口令的脚本）能过；学生账号走到这里会被打回门户，
    门户会告诉它"学生账号进不了管理端"。数据侧的真正兜底在 /api/admin/* 的 _guard。
    """
    if not _admin_authed(request):
        return _deny("admin")
    return FileResponse(str(config.WEB_DIR / "admin.html"),
                        headers=_NO_STORE)


# ==========================================================================
# 管理端 —— API
# ==========================================================================
# python-multipart 是 multipart 上传通道的依赖。没装时**不能**定义带 File/Form
# 的路由，否则 FastAPI 在导入期就抛 RuntimeError，整个服务都起不来。
# 因此这里条件注册；缺失时页面自动降级到零依赖的 /upload/raw 裸流通道。
try:
    import multipart as _multipart_mod  # noqa: F401
    _MULTIPART_OK = True
except Exception:
    _MULTIPART_OK = False


# ── 会话与权限判定 ────────────────────────────────────────────────────────
def _identity(request: Request) -> Optional[auth.Identity]:
    """
    从 Cookie 解出会话，再回查一次账号表。

    回查这一步是有意为之：管理端改了某人的学校名、或者把账号删了，
    已经登录的会话下一次请求就会立刻反映出来（删号即下线）。
    """
    got = auth.read(request.cookies.get(config.SESSION_COOKIE) or "")
    if not got:
        return None
    return auth.lookup(got[1])


def _api_token_ok(request: Request, token: str = "") -> bool:
    """无状态旁路：给脚本/第三方调用留的口令（CAMPUS_ADMIN_TOKEN）。"""
    if not config.ADMIN_TOKEN:
        return False
    got = (token
           or request.headers.get("X-Admin-Token")
           or request.query_params.get("token")
           or "").strip()
    return hmac.compare_digest(got, config.ADMIN_TOKEN)


def _student_authed(request: Request) -> bool:
    """学生端：任何登录身份都能进（三类角色都有 can_student）。"""
    return _identity(request) is not None


def _admin_authed(request: Request) -> bool:
    """管理端：超级管理员和学校管理员，或旁路口令。"""
    me = _identity(request)
    return (me is not None and me.can_admin) or _api_token_ok(request)


def _guard(request: Request, token: str = "") -> Optional[JSONResponse]:
    """管理接口的统一门卫。学生身份会被明确挡回去，而不是含糊地说'口令错误'。"""
    me = _identity(request)
    if (me is not None and me.can_admin) or _api_token_ok(request, token):
        return None
    if me is not None:
        return JSONResponse(
            {"ok": False, "error": "当前是学生账号，无权访问管理端"},
            status_code=403)
    return JSONResponse(
        {"ok": False, "error": "请先登录管理账号"},
        status_code=401)


# ── 登录 / 退出 / 会话查询 ────────────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str = ""
    password: str = ""
    target: str = "student"     # "student" | "admin" —— 想进哪扇门


class SchoolUpdateRequest(BaseModel):
    username: str = Field(..., min_length=1)
    school: str = Field(..., min_length=1, max_length=40)


@app.get("/api/auth/session")
async def auth_session(request: Request):
    """
    给前端问路用：我是谁？哪个学校的？能进哪扇门？
    门户和两个端页都靠它决定是"直接进"还是"弹登录框"。
    """
    me = _identity(request)
    if me is None:
        return {"ok": True, "role": None, "label": "访客", "school": "",
                "username": "", "name": "",
                "can_student": False, "can_admin": False, "is_super": False}
    return dict(ok=True, **me.as_dict())


@app.post("/api/auth/login")
async def auth_login(request: Request, req: LoginRequest = None):
    """
    账号密码登录。target 表示想进哪扇门：
      target=student —— 三类身份都放行；
      target=admin   —— 只认管理身份，学生账号会收到 403 和一句解释。
    """
    req = req or LoginRequest()
    me, err = auth.check(req.username, req.password)
    if me is None:
        return JSONResponse({"ok": False, "error": err}, status_code=401)

    target = "admin" if (req.target or "").strip() == "admin" else "student"
    if target == "admin" and not me.can_admin:
        return JSONResponse(
            {"ok": False,
             "error": "学生账号只能进入学生端；进入管理端请用管理员账号"},
            status_code=403)

    resp = JSONResponse(dict(ok=True, redirect="/admin" if target == "admin" else "/student",
                             **me.as_dict()))
    # 不设 HttpOnly 是刻意的：本机页面要能读它来决定 UI 显示什么身份。
    resp.set_cookie(config.SESSION_COOKIE, auth.issue(me.role, me.username),
                    max_age=config.SESSION_TTL, path="/", samesite="lax")
    return resp


@app.post("/api/auth/logout")
async def auth_logout():
    """退出登录：清掉会话，任何一扇门都要重新登录。"""
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(config.SESSION_COOKIE, path="/")
    return resp


@app.post("/api/auth/school")
async def auth_set_school(request: Request, req: SchoolUpdateRequest):
    """
    补填自己的学校名 —— 给"管理端开号时没写学校，学生端自己填"留的口子。
    只有普通账号能改（超级管理员没有所属学校），且只能改自己那一条。
    """
    me = _identity(request)
    if me is None:
        return JSONResponse({"ok": False, "error": "请先登录"}, status_code=401)
    if me.username != req.username:
        return JSONResponse({"ok": False, "error": "只能修改自己的学校名"},
                            status_code=403)
    if me.is_super or me.username == config.STUDENT_USER:
        return JSONResponse({"ok": False, "error": "内置账号的学校名请改 .env"},
                            status_code=400)
    ok, err = accounts.update_school(req.username, req.school)
    if not ok:
        return JSONResponse({"ok": False, "error": err}, status_code=400)
    return {"ok": True, "school": req.school.strip()}



class DeleteDocRequest(BaseModel):
    assistant: str = Field(..., min_length=1)
    filename: str = Field(..., min_length=1)


class AssistantUpsertRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    display_name: str = ""
    scope: str = ""
    description: str = ""
    samples: List[str] = Field(default_factory=list)


class AdminReindexRequest(BaseModel):
    only: Optional[List[str]] = None


@app.get("/api/admin/overview")
async def admin_overview(request: Request, token: str = ""):
    """概览：助手 / 文档 / 片段 / 问题文件 / 参数 / 能力清单。"""
    bad = _guard(request, token)
    if bad:
        return bad
    data = await asyncio.to_thread(admin.overview)
    data["capabilities"] = {
        "multipart": _MULTIPART_OK,
        "max_upload_mb": config.MAX_UPLOAD_MB,
        "parsable_ext": sorted(config.PARSABLE_EXT),
        "upload_url": "/api/admin/upload" if _MULTIPART_OK
                      else "/api/admin/upload/raw",
    }
    data["llm"] = await ollama.health()
    data["accounts"] = accounts.stats()
    # 问答记录统计也按范围给：学校管理员在概览上看到的"今日提问/没答出来"
    # 必须是本校的数，不能把兄弟学校的量算进来
    data["records"] = await asyncio.to_thread(
        records.summary, None, _record_scope(request, token))
    return data


# ── 问答记录：学生端问了什么，管理端在这里看 ──────────────────────────────
def _record_scope(request: Request, token: str = "") -> Optional[str]:
    """问答记录能看到哪个范围的记录。

    返回 None  = 不限（超级管理员、或带旁路口令的脚本调用）；
    返回 "某校" = 只看这个学校（学校管理员）。

    ⚠️ 这是**数据层**的收口：学生端接口不带学校过滤，所以就算绕过页面
    直接打这几个接口，学校管理员也拿不到别校的记录 —— 不能只靠前端藏。
    """
    if _api_token_ok(request, token):
        return None                       # 旁路口令视同超管
    me = _identity(request)
    if me is None or me.is_super:
        return None
    return me.school or ""                # 学校没填 → 只看"学校为空"的那批


@app.get("/api/admin/records")
async def admin_records(request: Request, token: str = "", limit: int = 50,
                        offset: int = 0, assistant: str = "", q: str = "",
                        miss: int = 0):
    """倒序翻问答记录，可按助手 / 关键词过滤，miss=1 只看没答出来的。

    超级管理员看全部；学校管理员只看本校的（见 _record_scope）。
    """
    bad = _guard(request, token)
    if bad:
        return bad
    sc = _record_scope(request, token)
    data = await asyncio.to_thread(
        records.query, limit=limit, offset=offset, assistant=assistant,
        keyword=q, miss_only=bool(miss), school=sc)
    data["assistants"] = await asyncio.to_thread(records.assistants, sc)
    return data


@app.delete("/api/admin/records")
async def admin_records_clear(request: Request, token: str = ""):
    """清空问答记录。

    超级管理员清全部；学校管理员**只清本校的**，别校的记录原样留着。
    """
    bad = _guard(request, token)
    if bad:
        return bad
    sc = _record_scope(request, token)
    n = await asyncio.to_thread(records.clear, sc)
    return {"ok": True, "removed": n, "scope": sc}


@app.get("/api/admin/records/export")
async def admin_records_export(request: Request, token: str = ""):
    """导出成 CSV（带 BOM，Excel 双击不乱码）。范围同列表接口。"""
    bad = _guard(request, token)
    if bad:
        return bad
    sc = _record_scope(request, token)
    rows = await asyncio.to_thread(records.export_rows, sc)
    name = "qa-records.csv" if sc is None else f"qa-records-{sc or '未填学校'}.csv"
    from urllib.parse import quote
    return Response(content=records.to_csv(rows),
                    media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             f"attachment; filename*=UTF-8''{quote(name)}"})


@app.get("/api/site")
async def site_public():
    """
    站点公开信息（不需要登录）—— 学生端进来先读这个，
    决定左上角要不要显示校园地图入口。没有配置就什么都不返回。
    """
    return {"ok": True, **site.public()}


# ── 账号管理：管理端现场开号，学生端拿号登录 ──────────────────────────────
class AccountCreateRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=24)
    password: str = Field(..., min_length=1, max_length=32)
    school: str = Field("", max_length=40)
    name: str = Field("", max_length=20)
    role: str = "student"


@app.get("/api/admin/accounts")
async def admin_account_list(request: Request, token: str = ""):
    """
    账号列表 = 内置账号（.env 里的超级管理员 / 演示学生）+ 管理端开出来的动态账号。
    内置的先列出来并标上 builtin，免得管理端看着空列表以为系统没配账号。
    """
    bad = _guard(request, token)
    if bad:
        return bad
    me = _identity(request)
    items = await asyncio.to_thread(accounts.list_all)
    builtin = [
        {"username": config.ADMIN_USER, "school": "", "name": "超级管理员",
         "role": "super", "enabled": True, "builtin": True,
         "created_at": "—", "password": ""},
        {"username": config.STUDENT_USER, "school": config.STUDENT_SCHOOL,
         "name": "演示学生", "role": "student", "enabled": True, "builtin": True,
         "created_at": "—", "password": ""},
    ]
    return {
        "accounts": builtin + items,
        "stats": await asyncio.to_thread(accounts.stats),
        "me": me.username if me else "",
        "schools": sorted({x.get("school", "") for x in items if x.get("school")}),
    }


@app.post("/api/admin/accounts")
async def admin_account_create(request: Request, req: AccountCreateRequest,
                               token: str = ""):
    """
    开一个新账号。开完把账号密码交给学生，他去门户登录即可。

    权限分层（前端会藏按钮，但真正的门在这里）：
      超级管理员   → 能开「学生」和「学校管理员」
      学校管理员   → 只能开「学生」，传 admin 会被 403 挡回去
    """
    bad = _guard(request, token)
    if bad:
        return bad
    me = _identity(request)
    wants_admin = (req.role or "").strip().lower() == accounts.ROLE_ADMIN
    i_am_super = (me is not None and me.is_super) or _api_token_ok(request, token)
    if wants_admin and not i_am_super:
        return JSONResponse(
            {"ok": False, "error": "只有超级管理员能开通「学校管理员」账号"},
            status_code=403)
    item, err = await asyncio.to_thread(
        accounts.create, req.username, req.password, req.school, req.name, req.role)
    if item is None:
        return JSONResponse({"ok": False, "error": err}, status_code=400)
    return {"ok": True, "account": item}


# ── 批量开号：管理员传一份 Excel / CSV，一次性把学生账号建好 ──────────────
def _read_table(data: bytes, filename: str) -> List[List[str]]:
    """把 xlsx / csv 读成二维字符串表。xlsx 用标准库解析（zip + XML），不引第三方依赖。"""
    name = (filename or "").lower()

    if name.endswith(".csv") or name.endswith(".txt"):
        text = None
        for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ValueError("CSV 编码无法识别，请另存为 UTF-8")
        rows = []
        for line in text.splitlines():
            if not line.strip():
                continue
            # 简单 CSV：支持双引号包裹的逗号
            cells, cur, in_q = [], "", False
            for ch in line:
                if ch == '"':
                    in_q = not in_q
                elif ch == "," and not in_q:
                    cells.append(cur); cur = ""
                else:
                    cur += ch
            cells.append(cur)
            rows.append([c.strip().strip('"').strip() for c in cells])
        return rows

    if not name.endswith(".xlsx"):
        raise ValueError("只支持 .xlsx 或 .csv")

    import io
    import re as _re
    import zipfile as _zip
    from xml.etree import ElementTree as _ET

    NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    z = _zip.ZipFile(io.BytesIO(data))

    shared: List[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        root = _ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in root.findall(NS + "si"):
            shared.append("".join(t.text or "" for t in si.iter(NS + "t")))

    sheets = [n for n in z.namelist() if _re.match(r"xl/worksheets/sheet\d+\.xml$", n)]
    if not sheets:
        raise ValueError("这个 xlsx 里没有工作表")
    root = _ET.fromstring(z.read(sorted(sheets)[0]))

    table: List[List[str]] = []
    for row in root.iter(NS + "row"):
        cells: Dict[int, str] = {}
        for c in row.findall(NS + "c"):
            ref = c.get("r") or ""
            m = _re.match(r"([A-Z]+)", ref)
            if not m:
                continue
            idx = 0
            for ch in m.group(1):
                idx = idx * 26 + (ord(ch) - 64)
            idx -= 1
            t = c.get("t")
            v = c.find(NS + "v")
            if t == "s" and v is not None and v.text:
                cells[idx] = shared[int(v.text)] if int(v.text) < len(shared) else ""
            elif t == "inlineStr":
                node = c.find(NS + "is")
                cells[idx] = "".join(x.text or "" for x in node.iter(NS + "t")) if node is not None else ""
            else:
                cells[idx] = v.text if (v is not None and v.text) else ""
        if cells:
            width = max(cells) + 1
            table.append([(cells.get(i) or "").strip() for i in range(width)])
    return table


# 表头里可能出现的中文叫法 → 内部字段
_COL_ALIAS = {
    "username": ("账号", "用户名", "学号", "登录名", "user", "username", "account"),
    "password": ("密码", "口令", "初始密码", "password", "pass"),
    "school": ("学校", "院校", "学校/大学", "校名", "school"),
    "name": ("姓名", "学生姓名", "名字", "name"),
    "role": ("身份", "角色", "类型", "role"),
}


@app.post("/api/admin/accounts/import")
async def admin_account_import(request: Request, file: UploadFile = File(...),
                               school: str = "", token: str = ""):
    """管理员传 Excel/CSV，批量开通账号。

    表头认这些写法（大小写与空格无所谓）：
        账号 / 学号  密码  学校  姓名  身份
    没有表头也行，但列顺序必须是：账号, 密码, 学校, 姓名, 身份。
    身份留空按「学生」处理；只有超级管理员能在表里写「管理员」。
    """
    bad = _guard(request, token)
    if bad:
        return bad

    me = _identity(request)
    i_am_super = (me is not None and me.is_super) or _api_token_ok(request, token)

    raw = await file.read()
    if not raw:
        return JSONResponse({"ok": False, "error": "文件是空的"}, status_code=400)
    if len(raw) > 5 * 1024 * 1024:
        return JSONResponse({"ok": False, "error": "文件超过 5 MB"}, status_code=400)

    try:
        table = await asyncio.to_thread(_read_table, raw, file.filename or "")
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"读取失败：{e}"}, status_code=400)

    table = [r for r in table if any(x.strip() for x in r)]
    if not table:
        return JSONResponse({"ok": False, "error": "表里没有内容"}, status_code=400)

    # 判断第一行是表头还是数据
    first = [c.strip().lower() for c in table[0]]
    header_map: Dict[str, int] = {}
    for field, aliases in _COL_ALIAS.items():
        for i, cell in enumerate(first):
            if cell in [a.lower() for a in aliases]:
                header_map[field] = i
                break
    has_header = "username" in header_map and "password" in header_map
    body = table[1:] if has_header else table
    if not has_header:
        header_map = {"username": 0, "password": 1, "school": 2, "name": 3, "role": 4}

    def pick(row: List[str], field: str) -> str:
        i = header_map.get(field, -1)
        return row[i].strip() if 0 <= i < len(row) else ""

    rows = [{
        "username": pick(r, "username"),
        "password": pick(r, "password"),
        "school": pick(r, "school") or school,
        "name": pick(r, "name"),
        "role": pick(r, "role"),
    } for r in body]

    if not rows:
        return JSONResponse({"ok": False, "error": "除了表头没有数据行"}, status_code=400)
    if len(rows) > 500:
        return JSONResponse({"ok": False, "error": "一次最多导入 500 个账号"}, status_code=400)

    result = await asyncio.to_thread(
        accounts.create_many, rows, school, i_am_super)
    return {
        "ok": True,
        "total": len(rows),
        "created": len(result["created"]),
        "skipped": len(result["skipped"]),
        "failed": len(result["failed"]),
        "detail": {
            "skipped": result["skipped"][:20],
            "failed": result["failed"][:20],
        },
    }


@app.delete("/api/admin/accounts")
async def admin_account_delete(request: Request, username: str, token: str = ""):
    """删账号。删掉之后该账号已登录的会话下一次请求就失效了。"""
    bad = _guard(request, token)
    if bad:
        return bad
    ok, err = await asyncio.to_thread(accounts.remove, username)
    if not ok:
        return JSONResponse({"ok": False, "error": err}, status_code=400)
    return {"ok": True}


# ── 站点设置：学生端左上角那个校园地图入口 ────────────────────────────────
class SiteSettingRequest(BaseModel):
    map_url: str = ""
    map_enabled: bool = False
    map_label: str = ""


@app.get("/api/admin/settings")
async def admin_settings_get(request: Request, token: str = ""):
    bad = _guard(request, token)
    if bad:
        return bad
    return {"ok": True, "settings": await asyncio.to_thread(site.load)}


@app.post("/api/admin/settings")
async def admin_settings_save(request: Request, req: SiteSettingRequest,
                              token: str = ""):
    """保存站点设置。地址只收 http(s)，开关没配地址会自动落回关闭。"""
    bad = _guard(request, token)
    if bad:
        return bad
    try:
        saved = await asyncio.to_thread(
            site.save, req.map_url, req.map_enabled, req.map_label)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    return {"ok": True, "settings": saved}


async def _finish_upload(result: dict, assistant: str = "", relpath: str = "") -> dict:
    """
    上传收尾，三种走法：

      显式指定 assistant  → 直接放那个分类（老的调用方式，仍然支持）
      带 relpath（拖文件夹）→ 取**顶层文件夹名**当分类，不再问模型
                             —— 用户既然已经在电脑上分好类了，就尊重他的分类
      什么都没带（零散文件）→ 读内容让模型判断归到哪一类

    DocSink 在没指定分类时已经把文件落在 data/incoming/ 暂存，
    这里决定归宿后搬进 data/raw/<分类>/。
    """
    result["need_reindex"] = True
    if (assistant or "").strip():
        return result

    staged = Path(result["path"])

    # ── 拖文件夹：按文件夹名归类 ──
    parts = PurePosixPath((relpath or "").replace("\\", "/")).parts
    folder = parts[0].strip() if len(parts) >= 2 else ""
    if folder:
        try:
            moved = await asyncio.to_thread(admin.file_into_category, staged, folder)
        except admin.AdminError as e:
            raise admin.AdminError(f"归类失败：{e}")
        moved["reason"] = f"folder: 按文件夹名归入「{moved['assistant']}」"
        moved["need_reindex"] = True
        return moved

    # ── 零散文件：模型判断 ──
    head = await asyncio.to_thread(classifier.read_head, staged)
    existing = await asyncio.to_thread(lambda: sorted(assistants.discover().keys()))
    label, why = await classifier.classify(staged.name, head, existing)

    try:
        moved = await asyncio.to_thread(admin.file_into_category, staged, label)
    except admin.AdminError as e:
        raise admin.AdminError(f"归类失败：{e}")

    moved["reason"] = why
    moved["content_read"] = bool(head)
    moved["need_reindex"] = True
    return moved


@app.post("/api/admin/upload/raw")
async def admin_upload_raw(request: Request, filename: str, assistant: str = "",
                           relpath: str = "", token: str = ""):
    """
    零依赖兜底通道：裸流上传，元数据走查询参数。
    服务端没装 python-multipart 时，页面自动走这条。

    assistant 留空 = 自动决定归宿；relpath 带目录时按顶层文件夹名分类。
    """
    bad = _guard(request, token)
    if bad:
        return bad

    try:
        hint = int(request.headers.get("content-length") or 0)
    except ValueError:
        hint = 0

    try:
        sink = admin.DocSink(assistant, filename, size_hint=hint)
    except admin.AdminError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    try:
        async for chunk in request.stream():
            sink.write(chunk)
        result = sink.commit()
        result = await _finish_upload(result, assistant, relpath)
    except admin.AdminError as e:
        sink.abort()
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except Exception as e:
        sink.abort()
        return JSONResponse({"ok": False, "error": f"上传失败：{e}"},
                            status_code=500)

    result["channel"] = "raw"
    return result


if _MULTIPART_OK:
    @app.post("/api/admin/upload")
    async def admin_upload(request: Request,
                           file: UploadFile = File(...),
                           assistant: str = Form(""),
                           relpath: str = Form(""),
                           token: str = Form("")):
        """标准 multipart 通道，支持浏览器原生进度事件。留空 assistant = 自动决定归宿。"""
        bad = _guard(request, token)
        if bad:
            return bad

        try:
            sink = admin.DocSink(assistant, file.filename or "")
        except admin.AdminError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

        try:
            while True:
                chunk = await file.read(1 << 20)      # 1 MB 一块，内存友好
                if not chunk:
                    break
                sink.write(chunk)
            result = sink.commit()
            result = await _finish_upload(result, assistant, relpath)
        except admin.AdminError as e:
            sink.abort()
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        except Exception as e:
            sink.abort()
            return JSONResponse({"ok": False, "error": f"上传失败：{e}"},
                                status_code=500)

        result["channel"] = "multipart"
        return result


@app.delete("/api/admin/documents")
async def admin_delete_document(request: Request, req: DeleteDocRequest,
                                token: str = ""):
    """删除一份文档：移入回收站 → 同步索引（有剩余文档则重建，否则清库）。"""
    bad = _guard(request, token)
    if bad:
        return bad

    try:
        result = await asyncio.to_thread(
            admin.delete_document, req.assistant, req.filename)
    except admin.AdminError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"删除失败：{e}"},
                            status_code=500)

    try:
        left = await asyncio.to_thread(admin.documents_remaining, req.assistant)
        if left > 0:
            await asyncio.to_thread(admin.rebuild, [req.assistant])
            result["index"] = f"已重建，剩余 {left} 份文档"
        else:
            await asyncio.to_thread(admin.drop_assistant_index, req.assistant)
            await asyncio.to_thread(admin.reload_only)
            result["index"] = "该助手文档已清空，索引已摘除"
    except Exception as e:
        await asyncio.to_thread(admin.reload_only)
        result["index"] = f"文档已删，但索引同步失败：{e}"

    return result


@app.get("/api/admin/trash")
async def admin_trash(request: Request, token: str = ""):
    bad = _guard(request, token)
    if bad:
        return bad
    return {"batches": await asyncio.to_thread(admin.list_trash),
            "dir": str(config.TRASH_DIR)}


@app.delete("/api/admin/trash")
async def admin_purge_trash(request: Request, batch: str, token: str = ""):
    """彻底清空某一批回收站内容（不可逆）。"""
    bad = _guard(request, token)
    if bad:
        return bad
    try:
        return await asyncio.to_thread(admin.purge_trash, batch)
    except admin.AdminError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@app.post("/api/admin/assistants")
async def admin_upsert_assistant(request: Request, req: AssistantUpsertRequest,
                                 token: str = ""):
    """新建或更新助手配置（展示名 / 职责 / 路由描述 / 示例问题）。"""
    bad = _guard(request, token)
    if bad:
        return bad
    try:
        r = await asyncio.to_thread(
            admin.upsert_assistant, req.name, req.display_name, req.scope,
            req.description, req.samples)
    except admin.AdminError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    # 配置直接影响路由描述，必须让链路立刻重新读一遍
    await asyncio.to_thread(admin.reload_only)
    r["note"] = "配置已生效（路由描述已刷新）"
    return r


@app.delete("/api/admin/assistants")
async def admin_delete_assistant(request: Request, name: str, token: str = ""):
    """删除整个助手：文档进回收站，索引与配置一并清除。"""
    bad = _guard(request, token)
    if bad:
        return bad
    try:
        r = await asyncio.to_thread(admin.delete_assistant, name)
    except admin.AdminError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    await asyncio.to_thread(admin.reload_only)
    return r


@app.post("/api/admin/reindex")
async def admin_reindex(request: Request, req: AdminReindexRequest = None,
                        token: str = ""):
    """重建索引（可只重建指定助手）。"""
    bad = _guard(request, token)
    if bad:
        return bad
    try:
        only = req.only if req else None
        manifest = await asyncio.to_thread(admin.rebuild, only)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    return {"ok": True, "manifest": manifest}


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def main(host: str = "127.0.0.1", port: int = 8000):
    import uvicorn
    print(config.describe())
    print(f"\n  打开浏览器访问 →  http://{host}:{port}\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="校园多助手问答服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    main(args.host, args.port)
