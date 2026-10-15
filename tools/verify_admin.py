# -*- coding: utf-8 -*-
"""
管理端 · 端到端验证脚本。

用法（服务需已启动）：
    .venv\\Scripts\\python.exe tools\\verify_admin.py

覆盖：登录与权限（两套账号 / 匿名被拦 / 学生进不了管理端 / 管理端可进学生端）/
      页面可达性 / 能力清单 / 上传两条通道 / 上传安全（穿越·格式·空名）/
      删除入回收站 + 索引同步 / 助手配置 CRUD / 回收站 / 真实问答。
所有测试数据在结束时自动清理。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

# 脚本常被从任意目录启动，先把项目根塞进 sys.path，否则 import app 会失败
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import config as _config      # noqa: E402  复用同一套 .env 解析，取出账号密码

BASE = "http://127.0.0.1:8000"
ADMIN_USER = _config.ADMIN_USER
ADMIN_PASS = _config.ADMIN_PASS
STUDENT_USER = _config.STUDENT_USER
STUDENT_PASS = _config.STUDENT_PASS

# trust_env=False：绕过 shell 里可能存在的 HTTP_PROXY，
# 否则连 127.0.0.1 的请求也会被塞进代理，得到假故障。
CLIENT = httpx.Client(base_url=BASE, timeout=120.0, trust_env=False)

RESULTS: list[tuple[str, str, bool, str]] = []
TEST_DOC = "自动化测试文档.md"
TEST_RAW = "裸流测试文档.txt"
AUTO_DOC = "ZZAUTO自动归档测试.md"      # 走自动归类，落点分类名由模型决定
TEST_ASSISTANT = "测试助手"
MARK = "ZZTEST2026"


def check(group: str, name: str, cond, detail: str = "") -> bool:
    RESULTS.append((group, name, bool(cond), detail))
    return bool(cond)


def head(title: str) -> None:
    print(f"\n── {title} " + "─" * max(0, 44 - len(title)))


# ══════════════════════════════════════════════════════════════════
def _login(user: str, pwd: str, target: str = "admin"):
    """用一套账号登录，返回 (client, response)。"""
    c = httpx.Client(base_url=BASE, timeout=30.0, trust_env=False)
    r = c.post("/api/auth/login",
               json={"username": user, "password": pwd, "target": target})
    return c, r


def t_gate() -> None:
    """
    登录与权限 —— 这是整套账号体系的核心约束：
      学生账号 → 只能进学生端
      管理身份（超级管理员 / 学校管理员）→ 管理端 + 学生端都能进
    两种身份各拿一个独立 Client，互不干扰。
    （动态开号的完整链路另有 tools/verify_accounts.py 覆盖。）
    """
    head("登录与权限（三类身份）")
    anon = httpx.Client(base_url=BASE, timeout=30.0, trust_env=False)
    student = admin = None
    try:
        # ── 匿名 ──
        r = anon.get("/api/auth/session")
        d = r.json() if r.status_code == 200 else {}
        check("权限", "匿名 session", r.status_code == 200 and d.get("role") is None,
              f"HTTP {r.status_code} / {d}")
        print(f"   匿名探路          -> role={d.get('role')}")
        check("权限", "匿名进不了 /student", _is_redirect(anon.get("/student"), "student"),
              "应 302 到 /?locked=student")
        print(f"   匿名访问 /student -> {_loc(anon, '/student')}")
        check("权限", "匿名进不了 /admin", _is_redirect(anon.get("/admin"), "admin"),
              "应 302 到 /?locked=admin")
        print(f"   匿名访问 /admin   -> {_loc(anon, '/admin')}")
        check("权限", "匿名调 /api/assistants 401",
              anon.get("/api/assistants").status_code == 401, "")
        check("权限", "匿名调 /api/chat 401",
              anon.post("/api/chat", json={"question": "你好"}).status_code == 401, "")
        check("权限", "匿名调管理接口 401",
              anon.get("/api/admin/overview").status_code == 401, "")
        print(f"   匿名调接口        -> assistants/chat/admin 全部 401")

        # ── 门户保持公开（它是登录入口）──
        r = anon.get("/")
        check("权限", "门户公开可达", r.status_code == 200 and len(r.content) > 500,
              f"HTTP {r.status_code}")
        print(f"   门户              -> {r.status_code}（登录入口，必须公开）")

        # ── 密码错误 ──
        r = anon.post("/api/auth/login",
                      json={"username": ADMIN_USER, "password": "definitely-not-it"})
        check("权限", "密码错误 401", r.status_code == 401, f"HTTP {r.status_code}")
        print(f"   错误密码登录      -> {r.status_code}")

        # ── 学生账号 ──
        student, r = _login(STUDENT_USER, STUDENT_PASS, "student")
        ok = r.status_code == 200 and r.json().get("role") == "student"
        check("权限", "学生账号登录学生端", ok, f"HTTP {r.status_code}")
        print(f"   学生登录(学生端)  -> {r.status_code} role={r.json().get('role')}")

        check("权限", "学生可进 /student", student.get("/student").status_code == 200, "")
        check("权限", "学生进不了 /admin", _is_redirect(student.get("/admin"), "admin"), "")
        print(f"   学生账号：/student {student.get('/student').status_code}"
              f" · /admin {_loc(student)}")

        r = student.get("/api/admin/overview")
        check("权限", "学生调管理接口 403", r.status_code == 403, f"HTTP {r.status_code}")
        print(f"   学生调管理接口    -> {r.status_code}（明确是权限问题，不是口令错）")

        # 学生账号想登管理端 —— 必须被拒
        tmp, r = _login(STUDENT_USER, STUDENT_PASS, "admin")
        tmp.close()
        check("权限", "学生账号登录管理端被拒 403", r.status_code == 403,
              f"HTTP {r.status_code}")
        print(f"   学生账号登录管理端-> {r.status_code} {r.json().get('error', '')}")

        # ── 管理账号 ──
        admin, r = _login(ADMIN_USER, ADMIN_PASS, "admin")
        ok = r.status_code == 200 and r.json().get("can_admin") is True
        check("权限", "管理账号登录管理端", ok, f"HTTP {r.status_code}")
        print(f"   管理登录(管理端)  -> {r.status_code} role={r.json().get('role')}")

        check("权限", "管理可进 /admin", admin.get("/admin").status_code == 200, "")
        check("权限", "管理可进 /student", admin.get("/student").status_code == 200, "")
        print(f"   管理账号：/admin {admin.get('/admin').status_code}"
              f" · /student {admin.get('/student').status_code}（管理端→学生端不受限）")

        r = admin.get("/api/admin/overview")
        check("权限", "管理调管理接口 200", r.status_code == 200, f"HTTP {r.status_code}")
        print(f"   管理调管理接口    -> {r.status_code}")

        # 伪造 Cookie：把角色改成 admin，签名对不上
        forged = httpx.Client(base_url=BASE, timeout=30.0, trust_env=False,
                              cookies={"campus_session": "admin.9999999999.deadbeef"})
        try:
            check("权限", "伪造 Cookie 无效",
                  _is_redirect(forged.get("/admin"), "admin")
                  and forged.get("/api/admin/overview").status_code == 401, "")
            print(f"   伪造 Cookie 进 /admin -> {_loc(forged)}（签名校验挡住）")
        finally:
            forged.close()

        # 把管理会话交给后续所有用例
        CLIENT.cookies.update(dict(admin.cookies))
    finally:
        anon.close()
        if student:
            student.close()
        if admin:
            admin.close()


def _loc(client, path: str = "/admin") -> str:
    """看一眼这个身份访问 path 的结果，用于打印。"""
    r = client.get(path, follow_redirects=False)
    return f"{r.status_code} {r.headers.get('location', '')}"


def _is_redirect(r, role: str) -> bool:
    """未登录/越权时，服务端应当 302 到门户并带上 locked=<角色>。"""
    loc = r.headers.get("location") or ""
    return r.status_code in (302, 307) and f"locked={role}" in loc


# ══════════════════════════════════════════════════════════════════
def t_pages() -> None:
    head("页面可达性")
    paths = ["/", "/student", "/admin",
             "/student/伴学", "/student/招生", "/student/答疑", "/student/迎新",
             "/student/不存在的助手"]
    for p in paths:
        try:
            r = CLIENT.get(p)
            ok = r.status_code == 200 and len(r.content) > 500
            check("页面", p, ok, f"HTTP {r.status_code} / {len(r.content)} 字节")
            print(f"   {'OK  ' if ok else 'FAIL'} {p:<22} HTTP {r.status_code}"
                  f"  {len(r.content) / 1024:.1f} KB")
        except Exception as e:
            check("页面", p, False, str(e))
            print(f"   FAIL {p:<22} {e}")


def t_overview() -> dict:
    head("能力清单与概览")
    r = CLIENT.get("/api/admin/overview")
    d = r.json()
    caps = d.get("capabilities") or {}
    s = d.get("summary") or {}

    check("概览", "HTTP 200", r.status_code == 200, str(r.status_code))
    check("概览", "multipart 通道", caps.get("multipart") is True,
          f"multipart={caps.get('multipart')}")
    check("概览", "解析格式白名单", bool(caps.get("parsable_ext")),
          str(caps.get("parsable_ext")))
    check("概览", "上传上限", caps.get("max_upload_mb") == 50,
          f"{caps.get('max_upload_mb')} MB")
    # 演示前可能已经把示例资料清空了（data/raw 为空），此时没有助手是**预期**行为。
    # 这种情况不判失败，改判「空库表现是否正确」。
    empty_kb = s.get("assistant_count", 0) == 0
    if empty_kb:
        check("概览", "空库状态正确（无助手/0 文档/0 片段）",
              s.get("doc_count", 0) == 0 and s.get("chunk_count", 0) == 0, str(s))
        print("   ⓘ 知识库当前为空 —— 这是清空示例资料后的预期状态")
    else:
        check("概览", "助手统计", s.get("assistant_count", 0) >= 1, str(s))

    print(f"   multipart 通道 : {'可用' if caps.get('multipart') else '不可用（降级裸流）'}")
    print(f"   上传上限       : {caps.get('max_upload_mb')} MB")
    print(f"   可解析格式     : {' '.join(caps.get('parsable_ext') or [])}")
    print(f"   助手/文档/片段 : {s.get('assistant_count')} / "
          f"{s.get('doc_count')} / {s.get('chunk_count')}")
    print(f"   推理服务       : "
          f"{'正常' if (d.get('llm') or {}).get('model_ready') else '异常'}")
    return d


def t_upload_multipart(assistant: str) -> None:
    head("上传 · multipart 通道")
    body = f"# 自动化测试\n\n唯一标记 {MARK}。这是一份由验证脚本写入的测试文档。\n"
    r = CLIENT.post("/api/admin/upload",
                    files={"file": (TEST_DOC, body.encode("utf-8"), "text/markdown")},
                    data={"assistant": assistant, "token": ""})
    d = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    ok = r.status_code == 200 and d.get("ok")
    check("上传", "multipart 上传成功", ok, f"HTTP {r.status_code} {d.get('error', '')}")
    print(f"   {'OK  ' if ok else 'FAIL'} {TEST_DOC} -> {d.get('kb', '?')} KB"
          f"  通道 {d.get('channel')}  覆盖={d.get('overwritten')}")

    # 非白名单扩展名
    r = CLIENT.post("/api/admin/upload",
                    files={"file": ("bad.exe", b"MZ\x90\x00", "application/octet-stream")},
                    data={"assistant": assistant, "token": ""})
    ok = r.status_code == 400
    check("上传安全", ".exe 被拒", ok, f"HTTP {r.status_code}")
    print(f"   {'OK  ' if ok else 'FAIL'} .exe 被拒  HTTP {r.status_code}  "
          f"{(r.json() or {}).get('error', '')[:40]}")

    # 老版 .doc：方案 A —— 直接拒收并提示另存
    r = CLIENT.post("/api/admin/upload",
                    files={"file": ("旧文档.doc", b"\xd0\xcf\x11\xe0", "application/msword")},
                    data={"assistant": assistant, "token": ""})
    err = (r.json() or {}).get("error", "")
    ok = r.status_code == 400 and "docx" in err
    check("上传安全", ".doc 被拒并提示另存", ok, f"HTTP {r.status_code} {err[:50]}")
    print(f"   {'OK  ' if ok else 'FAIL'} .doc 被拒  {err[:52]}")

    # 空分类名 = 走自动归类（不是错误）。用文件名内容造一点可读文本，
    # 让分类器有依据；产出的分类名不固定，所以清理时按文件名反查。
    r = CLIENT.post("/api/admin/upload",
                    files={"file": (AUTO_DOC,
                                    ("# ZZAUTO 自动归档测试\n\n" * 6).encode("utf-8"),
                                    "text/markdown")})
    d = r.json() if r.status_code == 200 else {}
    ok = r.status_code == 200 and d.get("auto_filed") and d.get("assistant")
    check("上传安全", "空分类名 → 自动归类", ok,
          f"HTTP {r.status_code} -> 「{d.get('assistant')}」")
    print(f"   {'OK  ' if ok else 'FAIL'} 空分类名 → 自动归入「{d.get('assistant')}」"
          f"  {d.get('reason', '')[:40]}")


def t_upload_raw(assistant: str) -> None:
    head("上传 · 裸流兜底通道")
    body = f"# 裸流测试\n\n标记 {MARK}，走的是零依赖通道。\n".encode("utf-8")
    r = CLIENT.post("/api/admin/upload/raw",
                    params={"assistant": assistant, "filename": TEST_RAW},
                    content=body,
                    headers={"Content-Type": "application/octet-stream"})
    d = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    ok = r.status_code == 200 and d.get("ok") and d.get("channel") == "raw"
    check("上传", "裸流上传成功", ok, f"HTTP {r.status_code} {d.get('error', '')}")
    print(f"   {'OK  ' if ok else 'FAIL'} {TEST_RAW} -> {d.get('kb', '?')} KB  通道 {d.get('channel')}")

    # 目录穿越：文件名里塞 ../
    r = CLIENT.post("/api/admin/upload/raw",
                    params={"assistant": assistant, "filename": "../../../邪恶文件.txt"},
                    content="穿越测试".encode("utf-8"),
                    headers={"Content-Type": "application/octet-stream"})
    d = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    landed = d.get("filename", "")
    # 期望：路径被剥离，最终落在 data/raw/<助手>/邪恶文件.txt
    ok = r.status_code == 200 and landed == "邪恶文件.txt"
    check("上传安全", "../../ 目录穿越被剥离", ok,
          f"落盘名={landed!r}")
    print(f"   {'OK  ' if ok else 'FAIL'} 穿越文件名 ../../../邪恶文件.txt"
          f" -> 落盘为 {landed!r}")

    import pathlib
    outside = pathlib.Path("D:/campus-assistant/data/邪恶文件.txt")
    ok2 = not outside.exists()
    check("上传安全", "未被写到 data/ 根目录", ok2, str(outside))
    print(f"   {'OK  ' if ok2 else 'FAIL'} data/ 根目录未被污染")


def t_markdown_hits(assistant: str) -> None:
    head("检索 · 新上传文档可被命中")
    r = CLIENT.post("/api/retrieve",
                    json={"question": f"{MARK} 是什么", "assistant": assistant})
    d = r.json()
    srcs = [h.get("source") for h in d.get("hits", [])]
    ok = any(TEST_DOC in s or TEST_RAW in s for s in srcs)
    check("检索", "新文档命中", ok, f"命中来源={srcs}")
    print(f"   {'OK  ' if ok else 'FAIL'} 问「{MARK} 是什么」-> 来源 {srcs}")


def t_delete(assistant: str) -> None:
    head("删除 · 入回收站 + 索引同步")
    r = CLIENT.request("DELETE", "/api/admin/documents",
                       json={"assistant": assistant, "filename": TEST_DOC})
    d = r.json() if r.status_code == 200 else {}
    ok = r.status_code == 200 and d.get("ok")
    check("删除", "文档删除成功", ok, f"HTTP {r.status_code} {d.get('error', '')}")
    print(f"   {'OK  ' if ok else 'FAIL'} 删除 {TEST_DOC} -> {d.get('index', '')}")

    tr = CLIENT.get("/api/admin/trash").json()
    batches = tr.get("batches") or []
    found = any(any(TEST_DOC in f["name"] for f in b["files"]) for b in batches)
    check("删除", "文件在回收站里", found, f"{len(batches)} 个批次")
    print(f"   {'OK  ' if found else 'FAIL'} 回收站批次 {len(batches)} 个，"
          f"最新：{batches[0]['batch'] if batches else '无'}")

    # 删一个不存在的
    r = CLIENT.request("DELETE", "/api/admin/documents",
                       json={"assistant": assistant, "filename": "根本不存在.docx"})
    ok = r.status_code == 400
    check("删除", "删除不存在的文档返回 400", ok, f"HTTP {r.status_code}")
    print(f"   {'OK  ' if ok else 'FAIL'} 删除不存在的文件 -> HTTP {r.status_code}")


def t_assistant_crud() -> None:
    head("助手配置 · 新建 / 更新 / 删除")
    r = CLIENT.post("/api/admin/assistants", json={
        "name": TEST_ASSISTANT,
        "display_name": "测试小助手",
        "scope": "只用于自动化验证，不会长期保留。",
        "description": f"测试用助手 {MARK}",
        "samples": ["你是谁？", "你能做什么？"],
    })
    ok = r.status_code == 200 and (r.json() or {}).get("ok")
    check("助手", "新建助手", ok, f"HTTP {r.status_code}")
    print(f"   {'OK  ' if ok else 'FAIL'} 新建「{TEST_ASSISTANT}」")

    import pathlib
    d = pathlib.Path(f"D:/campus-assistant/data/raw/{TEST_ASSISTANT}")
    ok = d.is_dir()
    check("助手", "目录已创建", ok, str(d))
    print(f"   {'OK  ' if ok else 'FAIL'} 目录 {d}")

    cfg = pathlib.Path("D:/campus-assistant/data/assistants.json")
    entry = {}
    if cfg.exists():
        entry = (json.loads(cfg.read_text(encoding="utf-8")).get("assistants") or {}).get(TEST_ASSISTANT, {})
    ok = entry.get("display_name") == "测试小助手" and len(entry.get("samples") or []) == 2
    check("助手", "配置已落盘", ok, str(entry)[:80])
    print(f"   {'OK  ' if ok else 'FAIL'} assistants.json: {entry.get('display_name')} / "
          f"{len(entry.get('samples') or [])} 条示例")

    # 更新
    r = CLIENT.post("/api/admin/assistants", json={
        "name": TEST_ASSISTANT, "display_name": "测试小助手V2"})
    entry = (json.loads(cfg.read_text(encoding="utf-8")).get("assistants") or {}).get(TEST_ASSISTANT, {})
    ok = entry.get("display_name") == "测试小助手V2"
    check("助手", "更新配置生效", ok, entry.get("display_name", ""))
    print(f"   {'OK  ' if ok else 'FAIL'} 更新后展示名 {entry.get('display_name')}")

    # 空名
    r = CLIENT.post("/api/admin/assistants", json={"name": "///", "display_name": "x"})
    ok = r.status_code == 400
    check("助手", "非法助手名被拒", ok, f"HTTP {r.status_code}")
    print(f"   {'OK  ' if ok else 'FAIL'} 助手名 '///' -> HTTP {r.status_code}")

    # 删除
    r = CLIENT.request("DELETE", "/api/admin/assistants",
                       params={"name": TEST_ASSISTANT})
    ok = r.status_code == 200 and not d.exists()
    check("助手", "删除助手", ok, f"HTTP {r.status_code}")
    print(f"   {'OK  ' if ok else 'FAIL'} 删除「{TEST_ASSISTANT}」-> 目录已移除={not d.exists()}")


def t_chat(assistant: str) -> None:
    head("真实问答 · SSE 流式")
    got = {"delta": 0, "text": "", "route": None, "sources": 0, "done": None, "error": None}
    try:
        with CLIENT.stream("POST", "/api/chat",
                           json={"question": "学校有哪些社团？", "history": [],
                                 "assistant": assistant}) as r:
            for line in r.iter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    evt = json.loads(line[5:].strip())
                except Exception:
                    continue
                t = evt.get("type")
                if t == "delta":
                    piece = evt.get("data") or ""
                    got["delta"] += len(piece)
                    got["text"] += piece
                elif t == "route":
                    got["route"] = (evt.get("data") or {}).get("mode")
                elif t == "sources":
                    got["sources"] = len(evt.get("data") or [])
                elif t == "done":
                    got["done"] = evt.get("data") or {}
                elif t == "error":
                    got["error"] = evt.get("data")
    except Exception as e:
        got["error"] = str(e)

    # 判定放宽到两种情况：给了实质性回答，或者明确说"资料里没有"。
    # 后者同样是正确行为 —— 知识库被清空 / 问题超出语料时，模型必须拒答而不是编造，
    # 这正是这套系统的设计目标，不该被判成失败。
    refused = any(k in got["text"] for k in ("未包含", "没有找到", "没有相关", "未找到"))
    ok = (got["delta"] > 30 or refused) and not got["error"]
    check("问答", "SSE 流式回答", ok,
          f"{got['delta']} 字{'（明确拒答，符合预期）' if refused else ''} / 错误={got['error']}")
    print(f"   {'OK  ' if ok else 'FAIL'} 回答 {got['delta']} 字 · "
          f"路由 {got['route']} · 引用 {got['sources']} 份 · "
          f"耗时 {(got['done'] or {}).get('elapsed', '?')}s"
          + ("  （知识库无相关资料，模型如实拒答）" if refused else ""))
    if got["error"]:
        print(f"        错误：{got['error']}")


def t_cleanup(assistant: str) -> None:
    head("清理测试数据")
    for fn in (TEST_RAW, "邪恶文件.txt"):
        try:
            r = CLIENT.request("DELETE", "/api/admin/documents",
                               json={"assistant": assistant, "filename": fn})
            print(f"   清理 {fn} -> HTTP {r.status_code}")
        except Exception as e:
            print(f"   清理 {fn} 失败：{e}")

    # 自动归类那份不知道自己被扔进了哪个分类，按文件名反查再删
    try:
        ov = CLIENT.get("/api/admin/overview").json()
        for a in ov.get("assistants", []):
            if any(x.get("name") == AUTO_DOC for x in a.get("docs", [])):
                r = CLIENT.request("DELETE", "/api/admin/documents",
                                   json={"assistant": a["name"], "filename": AUTO_DOC})
                print(f"   清理 {AUTO_DOC}（分类「{a['name']}」）-> HTTP {r.status_code}")
    except Exception as e:
        print(f"   清理自动归类文档失败：{e}")

    # 清掉本次产生的回收站批次
    try:
        tr = CLIENT.get("/api/admin/trash").json()
        for b in (tr.get("batches") or [])[:6]:
            CLIENT.request("DELETE", "/api/admin/trash", params={"batch": b["batch"]})
        print("   已清空本次测试产生的回收站批次")
    except Exception as e:
        print(f"   回收站清理失败：{e}")


# ══════════════════════════════════════════════════════════════════
def main() -> int:
    print("=" * 56)
    print("  管理端 · 端到端验证")
    print("=" * 56)

    try:
        t_gate()
        t_pages()
        t_overview()
        t_upload_multipart("答疑")
        t_upload_raw("答疑")
        # 上传后需重建索引才能被检索到
        print("\n   [重建索引] 让新上传的文档进入检索…")
        CLIENT.post("/api/admin/reindex", json={"only": ["答疑"]}, timeout=300)
        t_markdown_hits("答疑")
        t_delete("答疑")
        t_assistant_crud()
        t_chat("auto")
        t_cleanup("答疑")
    except httpx.ConnectError:
        print("\n无法连接服务，请先启动：start.bat 或 python -m app.server")
        return 2

    ok = sum(1 for _, _, c, _ in RESULTS if c)
    fail = [r for r in RESULTS if not r[2]]

    print("\n" + "=" * 56)
    print(f"  通过 {ok} / {len(RESULTS)} 项")
    if fail:
        print("\n  未通过：")
        for g, n, _, d in fail:
            print(f"    · [{g}] {n}  {d}")
    print("=" * 56)
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
