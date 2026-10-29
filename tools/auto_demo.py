# -*- coding: utf-8 -*-
"""自动演示：按分镜顺序把学生端、管理端跑一遍，供录屏使用。

用法（服务要先起来）：
    .venv\\Scripts\\python.exe tools/auto_demo.py            # 从门户开始，全自动
    .venv\\Scripts\\python.exe tools/auto_demo.py --from 3   # 从第 3 幕开始（调试用）
    .venv\\Scripts\\python.exe tools/auto_demo.py --slow     # 每步多停 1 秒，方便配音

设计要点：
  · 用可见 Chrome（不是 headless），你能直接录屏；
  · 鼠标移动是插值的，不是瞬间跳，录像里看着自然；
  · 输入框是逐字敲进去的，不是一次性粘贴；
  · 每幕之间有停顿，留出旁白的呼吸时间。

配音建议：先跑一遍记下每幕时间点，再对着这条时间轴念。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
import http.cookiejar

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp_shot import CHROME, http_json, WS          # 复用已有的最小 CDP 客户端

PORT = 9333
PROFILE = r"D:\campus-assistant\.workbuddy\tmp\autodemo-profile"
BASE = "http://127.0.0.1:8000"

SLOW = 0.0          # --slow 时每步额外等待的秒数


# ── 基础设施 ────────────────────────────────────────────────────────────────
def login_cookie(username: str, password: str) -> str:
    req = urllib.request.Request(
        BASE + "/api/auth/login",
        data=json.dumps({"username": username, "password": password}).encode(),
        headers={"Content-Type": "application/json"})
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cj),
        urllib.request.ProxyHandler({}))
    opener.open(req, timeout=10).read()
    for c in cj:
        if c.name == "campus_session":
            return c.value
    raise RuntimeError("登录失败，拿不到会话 cookie")


class Demo:
    def __init__(self, ws: WS):
        self.ws = ws
        self.w = 1440
        self.h = 900
        self._mouse = (self.w // 2, self.h // 2)

    # ---- 基础动作 ----
    def wait(self, sec: float) -> None:
        time.sleep(sec + SLOW)

    def goto(self, url: str, wait: float = 2.5) -> None:
        self.ws.call("Page.navigate", {"url": url})
        self.wait(wait)

    def move_to(self, x: int, y: int, steps: int = 18, pause: float = 0.35) -> None:
        """把鼠标平滑挪到 (x, y)，录像里能看到轨迹而不是瞬移。"""
        x0, y0 = self._mouse
        for i in range(1, steps + 1):
            t = i / steps
            # ease-out，前快后慢，接近真人
            k = 1 - (1 - t) ** 2
            cx = int(x0 + (x - x0) * k)
            cy = int(y0 + (y - y0) * k)
            self.ws.call("Input.dispatchMouseEvent", {
                "type": "mouseMoved", "x": cx, "y": cy, "buttons": 0})
            time.sleep(0.012)
        self._mouse = (x, y)
        time.sleep(pause)

    def click_at(self, x: int, y: int) -> None:
        self.move_to(x, y)
        for kind in ("mousePressed", "mouseReleased"):
            self.ws.call("Input.dispatchMouseEvent", {
                "type": kind, "x": x, "y": y,
                "button": "left", "clickCount": 1, "buttons": 1 if kind == "mousePressed" else 0})
            time.sleep(0.06)
        time.sleep(0.35)

    def click_sel(self, selector: str, wait: float = 0.8) -> bool:
        """点一个有 CSS 选择器的元素：先取它的中心坐标，再真实点击。"""
        box = self.rect(selector)
        if not box:
            print(f"    [跳过] 找不到 {selector}")
            return False
        self.click_at(box["cx"], box["cy"])
        self.wait(wait)
        return True

    def rect(self, selector: str):
        expr = (
            "(()=>{const e=document.querySelector(%r); if(!e) return '';"
            "const r=e.getBoundingClientRect();"
            "if(r.width===0||r.height===0) return '';"
            "return JSON.stringify({cx:Math.round(r.x+r.width/2),"
            "cy:Math.round(r.y+r.height/2),w:Math.round(r.width),h:Math.round(r.height)});})()"
            % selector)
        v = self.js(expr)
        if not v:
            return None
        try:
            return json.loads(v)
        except Exception:
            return None

    def js(self, expr: str):
        r = self.ws.call("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        return (r.get("result") or {}).get("value")

    def type_text(self, selector: str, text: str, per_char: float = 0.045) -> None:
        """点进输入框，然后逐字敲进去。"""
        if not self.click_sel(selector, wait=0.3):
            return
        self.js(f"(()=>{{const e=document.querySelector({selector!r}); e.value=''; e.focus();}})()")
        for ch in text:
            self.ws.call("Input.insertText", {"text": ch})
            time.sleep(per_char)
        time.sleep(0.25)

    def press_enter(self) -> None:
        """回车发送。注意 CDP 不接受 text=None，所以按类型分开传。"""
        base = {"key": "Enter", "code": "Enter",
                "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13}
        for kind in ("rawKeyDown", "char", "keyUp"):
            params = dict(base, type=kind)
            if kind == "char":
                params["text"] = "\r"
            self.ws.call("Input.dispatchKeyEvent", params)
            time.sleep(0.05)
        time.sleep(0.3)

    def scroll_by(self, dy: int, ms: int = 900) -> None:
        """平滑滚动，分 20 帧推完，录像里不会一跳一跳。"""
        frames = 20
        step = dy / frames
        for _ in range(frames):
            self.js(f"window.scrollBy(0, {step:.1f})")
            time.sleep(ms / frames / 1000)

    def scroll_to(self, selector: str, ms: int = 900) -> None:
        self.js(f"(()=>{{const e=document.querySelector({selector!r});"
                f"if(e) e.scrollIntoView({{behavior:'smooth',block:'center'}});}})()")
        time.sleep(ms / 1000)

    def hint(self, text: str) -> None:
        """在页面右下角显示当前这一幕的提示，同时打印时间戳，方便配音对齐。"""
        if not hasattr(self, "_t0"):
            self._t0 = time.time()
        el = time.time() - self._t0
        print(f"    [{int(el)//60}:{int(el)%60:02d}] {text}", flush=True)
        self.js("(()=>{let d=document.getElementById('__demo_hint');"
                "if(!d){d=document.createElement('div');d.id='__demo_hint';"
                "d.style.cssText='position:fixed;right:18px;bottom:16px;z-index:99999;"
                "padding:9px 14px;border-radius:10px;background:rgba(0,0,0,.72);color:#fff;"
                "font:13px/1.5 system-ui,sans-serif;pointer-events:none';document.body.appendChild(d);}"
                f"d.textContent={text!r};}})()")

    def hide_hint(self) -> None:
        self.js("(()=>{const d=document.getElementById('__demo_hint'); d&&d.remove();})()")

    def wait_answer(self, timeout: float = 90) -> None:
        """等流式回答结束（页面上的“停止/生成中”消失，或出现来源标注）。"""
        t0 = time.time()
        while time.time() - t0 < timeout:
            busy = self.js(
                "(()=>{const s=document.querySelector('#send');"
                "return s && (s.disabled || s.classList.contains('busy'));})()")
            if not busy:
                time.sleep(1.2)
                return
            time.sleep(0.5)
        print("    [提示] 等回答超时，继续往下走")


# ── 各幕 ────────────────────────────────────────────────────────────────────
def act_portal(d: Demo) -> None:
    d.hint("① 门户：说清两件事——信息散、通用模型不可信")
    d.goto(BASE + "/", 3.0)
    # 主标题停一下
    d.move_to(700, 300, pause=1.2)
    d.move_to(1120, 130, pause=1.5)      # 右上角“服务正常 · 3 个分类就绪”
    d.move_to(430, 560, pause=1.2)       # 学生端卡片
    d.move_to(1010, 560, pause=1.2)      # 管理端卡片
    d.wait(1.5)


def act_student_login(d: Demo, cookie: str) -> None:
    d.hint("② 学生端：学校账号登录，不用先选分类")
    d.ws.call("Network.setCookie", {
        "name": "campus_session", "value": cookie,
        "domain": "127.0.0.1", "path": "/"})
    d.goto(BASE + "/student", 3.5)
    d.move_to(378, 235, pause=0.9)       # 小助手卡片（自动路由）
    d.move_to(378, 468, pause=0.7)       # 当前登录身份
    d.wait(1.2)


def act_ask(d: Demo, question: str, hint: str) -> None:
    d.hint(hint)
    box = d.rect("#input") or {"cx": 720, "cy": 800}
    d.click_at(box["cx"], box["cy"])
    d.type_text("#input", question, per_char=0.05)
    d.wait(0.5)
    d.press_enter()
    d.wait(1.5)
    d.wait_answer()


def act_show_trace(d: Demo) -> None:
    d.hint("③ 参考资料与决策链：答案是能查的")
    # 展开“参考资料”
    for sel in (".src-toggle", "#refs-toggle"):
        if d.click_sel(sel, wait=1.6):
            break
    d.wait(1.6)
    # 展开“决策链”
    for sel in (".trace-toggle", "#trace-toggle"):
        if d.click_sel(sel, wait=1.8):
            break
    d.wait(1.8)


def act_admin(d: Demo, admin_cookie: str) -> None:
    d.hint("④ 管理端：概览 → 上传 → 知识库 → 账号 → 记录")
    d.ws.call("Network.setCookie", {
        "name": "campus_session", "value": admin_cookie,
        "domain": "127.0.0.1", "path": "/"})
    d.goto(BASE + "/admin", 3.5)

    # 概览：卡片逐个数过去
    for x in (300, 520, 740, 960, 1180):
        d.move_to(x, 250, pause=0.55)
    d.scroll_by(0)  # 占位，让视线停在原处
    d.wait(1.0)

    # 上传文档
    d.click_sel('[data-view="upload"]', wait=2.0)
    d.move_to(720, 420, pause=1.4)       # 拖拽区
    d.wait(1.2)

    # 知识库
    d.click_sel('[data-view="kb"]', wait=2.2)
    d.scroll_by(300, 1100)
    d.wait(1.3)

    # 学校账号
    d.click_sel('[data-view="accounts"]', wait=2.0)
    d.move_to(720, 430, pause=1.2)
    d.wait(1.0)

    # 问答记录
    d.click_sel('[data-view="records"]', wait=2.2)
    d.scroll_by(320, 1100)
    d.wait(1.4)


def act_map(d: Demo, admin_cookie: str) -> None:
    d.hint("⑤ 校园地图：填链接 → 学生端就能打开")
    d.click_sel('[data-view="plugin"]', wait=2.0)
    d.move_to(700, 330, pause=1.2)       # 链接输入框
    d.move_to(700, 470, pause=1.0)       # 开关
    d.wait(1.5)


def act_student_map(d: Demo, cookie: str) -> None:
    d.hint("⑥ 回到学生端：侧栏多了一个入口")
    d.ws.call("Network.setCookie", {
        "name": "campus_session", "value": cookie,
        "domain": "127.0.0.1", "path": "/"})
    d.goto(BASE + "/student", 3.0)
    box = d.rect("#plugin-entry")
    if box:
        d.move_to(box["cx"], box["cy"], pause=1.8)
    d.wait(1.2)


def act_end(d: Demo) -> None:
    d.hint("⑦ 收尾：回到门户主标题")
    d.hide_hint()
    d.goto(BASE + "/", 2.5)
    d.move_to(700, 300, pause=2.0)
    d.wait(2.0)


# ── 主流程 ──────────────────────────────────────────────────────────────────
def main() -> int:
    global SLOW
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", type=int, default=1,
                    help="从第几幕开始（1 门户 2 登录 3 提问 4 追踪 5 管理端 6 地图 7 收尾）")
    ap.add_argument("--slow", action="store_true", help="每步多停 1 秒，方便配音")
    ap.add_argument("--student", default="lixiaoyu", help="学生账号")
    ap.add_argument("--student-pass", default="2003lyx0721")
    ap.add_argument("--admin", default="admin")
    ap.add_argument("--admin-pass", default="admin11")
    args = ap.parse_args()
    if args.slow:
        SLOW = 1.0

    # 服务得先活着
    try:
        urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
            BASE + "/api/status", timeout=5).read()
    except Exception:
        print("✗ 服务没起来。先双击 start.bat，再跑这个脚本。")
        return 1

    print("→ 登录两个账号 ...")
    stu_cookie = login_cookie(args.student, args.student_pass)
    adm_cookie = login_cookie(args.admin, args.admin_pass)
    print("  OK")

    os.makedirs(PROFILE, exist_ok=True)
    print("→ 启动可见 Chrome（不要关它，录屏就录这个窗口）...")
    proc = subprocess.Popen([
        CHROME, "--no-first-run", "--no-default-browser-check",
        "--no-proxy-server", "--proxy-bypass-list=*",
        "--disable-features=Translate", "--hide-crash-restore-bubble",
        f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE}",
        "--window-position=60,40", "--window-size=1460,960",
        "--app=about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    ws_url = None
    for _ in range(60):
        time.sleep(0.5)
        try:
            pages = [t for t in http_json(f"http://127.0.0.1:{PORT}/json/list")
                     if t.get("type") == "page"]
            if pages:
                ws_url = pages[0]["webSocketDebuggerUrl"]
                break
        except Exception:
            pass
    if not ws_url:
        print("✗ Chrome 没起来")
        proc.kill()
        return 1

    ws = WS(ws_url)
    ws.call("Page.enable")
    ws.call("Runtime.enable")
    ws.call("Network.enable")
    d = Demo(ws)

    t0 = time.time()
    try:
        if args.start <= 1:
            act_portal(d)
        if args.start <= 2:
            act_student_login(d, stu_cookie)
        if args.start <= 3:
            act_ask(d, "奖学金怎么评", "③ 第一问：它会自己判断该找哪个助手")
        if args.start <= 4:
            act_show_trace(d)
            act_ask(d, "新生报到需要带哪些材料", "④ 换个话题，它自己换了个助手")
        if args.start <= 5:
            act_admin(d, adm_cookie)
        if args.start <= 6:
            act_map(d, adm_cookie)
            act_student_map(d, stu_cookie)
        act_end(d)
    finally:
        print(f"\n✓ 跑完，用时 {time.time()-t0:.0f} 秒")
        print("  窗口留着方便你补录；关掉它这个脚本就结束了。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
