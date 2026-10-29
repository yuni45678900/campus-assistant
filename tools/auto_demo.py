# -*- coding: utf-8 -*-
"""自动演示 · 按宇哥定的录制顺序走一遍，供录屏配音。

顺序：
    1. 首页，讲两句
    2. 点管理端 → 真输密码 admin11 → 进入
    3. 概览（此时是空的）
    4. 上传「伴学 / 招生 / 迎新」三个文件夹
    5. 回概览，数据出来了
    6. 学校账号：开通 A大学 学生「王五」，顺手点「下载模板」
    7. 校园地图（**这段留给你自己录，脚本可以停在这儿等你**）
    8. 系统状态：运行参数、回收站、危险操作（只看，不点）
    9. 学生端：点「迎新助手」→ 常见问题里找「新生报到时间」
   10. 再问「奖学金怎么评」，看自动路由
   11. GitHub 开源页

用法：
    .venv\\Scripts\\python.exe tools/auto_demo.py
    .venv\\Scripts\\python.exe tools/auto_demo.py --from 6
    .venv\\Scripts\\python.exe tools/auto_demo.py --slow
    .venv\\Scripts\\python.exe tools/auto_demo.py --pause-at 7 --from 7

录之前记得清空资料（demo_reset.bat），这样「传之前空、传之后有」的对比才成立。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp_shot import CHROME, http_json, WS

PORT = 9333
PROFILE = r"D:\campus-assistant\.workbuddy\tmp\autodemo-profile"
BASE = "http://127.0.0.1:8000"
ROOT = r"D:\campus-assistant"
MATERIAL = os.path.join(ROOT, "演示素材")
GITHUB = "https://github.com/yuni45678900/campus-assistant"

SLOW = 0.0
PAUSE_AT = 0
CURRENT = [0]


def net_ok() -> bool:
    try:
        urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
            BASE + "/api/status", timeout=5).read()
        return True
    except Exception:
        return False


class Demo:
    def __init__(self, ws: WS):
        self.ws = ws
        self._mouse = (720, 450)
        self._t0 = time.time()

    # ── 基础动作 ─────────────────────────────────────────
    def wait(self, sec: float) -> None:
        time.sleep(sec + SLOW)

    def say(self, text: str) -> None:
        """打印这一步该讲什么，同时贴在页面右下角。"""
        el = time.time() - self._t0
        print(f"  [{int(el)//60}:{int(el)%60:02d}] {text}", flush=True)
        self.js("(()=>{let d=document.getElementById('__hint');"
                "if(!d){d=document.createElement('div');d.id='__hint';"
                "d.style.cssText='position:fixed;right:16px;bottom:14px;z-index:99999;"
                "padding:8px 13px;border-radius:9px;background:rgba(0,0,0,.7);color:#fff;"
                "font:12.5px/1.5 system-ui,sans-serif;max-width:520px;pointer-events:none';"
                "document.body.appendChild(d);}"
                f"d.textContent={text!r};}})()")

    def hide_hint(self) -> None:
        self.js("(()=>{const d=document.getElementById('__hint'); d&&d.remove();})()")

    def js(self, expr: str):
        r = self.ws.call("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        return (r.get("result") or {}).get("value")

    def goto(self, url: str, wait: float = 2.6) -> None:
        self.ws.call("Page.navigate", {"url": url})
        self.wait(wait)

    def rect(self, sel: str):
        v = self.js(
            "(()=>{const e=document.querySelector(%r); if(!e) return '';"
            "const r=e.getBoundingClientRect();"
            "if(r.width===0||r.height===0) return '';"
            "return JSON.stringify({cx:Math.round(r.x+r.width/2),"
            "cy:Math.round(r.y+r.height/2)});})()" % sel)
        return json.loads(v) if v else None

    def move_to(self, x: int, y: int, steps: int = 16, pause: float = 0.32) -> None:
        x0, y0 = self._mouse
        for i in range(1, steps + 1):
            k = 1 - (1 - i / steps) ** 2          # ease-out，像人手
            self.ws.call("Input.dispatchMouseEvent", {
                "type": "mouseMoved",
                "x": int(x0 + (x - x0) * k), "y": int(y0 + (y - y0) * k),
                "buttons": 0})
            time.sleep(0.012)
        self._mouse = (x, y)
        time.sleep(pause)

    def click_at(self, x: int, y: int) -> None:
        self.move_to(x, y)
        for kind in ("mousePressed", "mouseReleased"):
            self.ws.call("Input.dispatchMouseEvent", {
                "type": kind, "x": x, "y": y, "button": "left",
                "clickCount": 1, "buttons": 1 if kind == "mousePressed" else 0})
            time.sleep(0.06)
        time.sleep(0.3)

    def click(self, sel: str, wait: float = 0.9) -> bool:
        b = self.rect(sel)
        if not b:
            print(f"      (没找到 {sel})")
            return False
        self.click_at(b["cx"], b["cy"])
        self.wait(wait)
        return True

    def type_into(self, sel: str, text: str, cps: float = 0.07) -> None:
        """点进输入框逐字敲 —— 密码框也这么输，镜头里能看到一个个字符出来。"""
        if not self.click(sel, wait=0.35):
            return
        self.js(f"(()=>{{const e=document.querySelector({sel!r});e.value='';e.focus();}})()")
        for ch in text:
            self.ws.call("Input.insertText", {"text": ch})
            time.sleep(cps)
        self.wait(0.3)

    def enter(self) -> None:
        base = {"key": "Enter", "code": "Enter",
                "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13}
        for kind in ("rawKeyDown", "char", "keyUp"):
            p = dict(base, type=kind)
            if kind == "char":
                p["text"] = "\r"
            self.ws.call("Input.dispatchKeyEvent", p)
            time.sleep(0.05)
        self.wait(0.3)

    def scroll(self, dy: int, ms: int = 900) -> None:
        for _ in range(20):
            self.js(f"window.scrollBy(0, {dy/20:.1f})")
            time.sleep(ms / 20000)

    def upload_paths(self, sel: str, paths) -> bool:
        """把真实文件塞进 <input type=file>，CDP 支持多文件。"""
        doc = self.ws.call("DOM.getDocument", {"depth": -1})
        node = self.ws.call("DOM.querySelector",
                            {"nodeId": doc["root"]["nodeId"], "selector": sel})
        nid = node.get("nodeId")
        if not nid:
            print(f"      (上传控件 {sel} 没找到)")
            return False
        self.ws.call("DOM.setFileInputFiles", {"files": list(paths), "nodeId": nid})
        return True

    def wait_answer(self, timeout: float = 120) -> None:
        t = time.time()
        while time.time() - t < timeout:
            busy = self.js("(()=>{const s=document.querySelector('#send');"
                           "return s && (s.disabled || s.classList.contains('busy'));})()")
            if not busy:
                time.sleep(1.5)
                return
            time.sleep(0.5)
        print("      (等回答超时，继续往下走)")


def collect_folder(folder: str):
    out = []
    for cur, _d, files in os.walk(os.path.join(MATERIAL, folder)):
        for f in files:
            if f.lower().endswith((".docx", ".md", ".pdf", ".txt")):
                out.append(os.path.join(cur, f))
    return out


# ── 十一步 ──────────────────────────────────────────────────────────────────
def step_home(d: Demo) -> None:
    d.say("① 首页：校园信息散在各部门，缺一个统一入口")
    d.goto(BASE + "/", 3.2)
    d.move_to(700, 300, pause=1.6)
    d.move_to(1130, 128, pause=1.4)
    d.move_to(420, 560, pause=1.2)
    d.move_to(1010, 560, pause=1.2)
    d.wait(1.0)


def step_admin_login(d: Demo) -> None:
    d.say("② 点管理端，输入管理员密码进入")
    d.click("#admin-card", wait=1.6)
    # 门户那张卡是 <a href="/admin">，点了会直接跳；跳过去未登录会弹登录框。
    # 万一没跳（有的浏览器把 a 的跳转吞了），这里补一次导航。
    # 等登录框出现（跳转+渲染要一点时间），最多等 8 秒
    for _ in range(16):
        if d.rect("#gate-user"):
            break
        d.wait(0.5)
    if not d.rect("#gate-user"):
        d.goto(BASE + "/admin", 2.5)
        for _ in range(16):
            if d.rect("#gate-user"):
                break
            d.wait(0.5)
    d.wait(0.8)
    d.type_into("#gate-user", "admin", cps=0.09)
    d.type_into("#gate-pwd", "admin11", cps=0.09)
    d.wait(0.6)
    d.click("#gate-go", wait=3.2)


def step_overview_empty(d: Demo) -> None:
    d.say("③ 概览：还没传资料，数字都是零")
    for x in (300, 520, 740, 960, 1180):
        d.move_to(x, 248, pause=0.5)
    d.wait(1.8)


def step_upload(d: Demo) -> None:
    d.say("④ 把伴学、招生、迎新三个文件夹拖进来")
    d.click('[data-view="upload"]', wait=2.2)
    d.wait(0.8)
    for folder in ("伴学", "招生", "迎新"):
        paths = collect_folder(folder)
        if not paths:
            print(f"      ({folder} 没素材，跳过)")
            continue
        print(f"      {folder}: {len(paths)} 个文件")
        d.click("#pick-dir", wait=0.7)        # 镜头上有个“选文件夹”的动作
        if d.upload_paths("#dir-input", paths):
            d.wait(2.4)
        d.wait(0.6)
    d.click("#do-upload", wait=3.0)
    d.wait(3.5)                               # 等上传与后台建索引


def step_overview_full(d: Demo) -> None:
    d.say("⑤ 回概览：文档数、片段数都上来了")
    d.click('[data-view="overview"]', wait=2.8)
    for x in (300, 520, 740, 960, 1180):
        d.move_to(x, 248, pause=0.55)
    d.scroll(260, 1000)
    d.wait(1.8)


def step_account(d: Demo) -> None:
    d.say("⑥ 开通 A大学学生「王五」；也可以传 Excel 批量开")
    for _ in range(12):                      # 等导航渲染出来
        if d.rect('[data-view="accounts"]'):
            break
        d.wait(0.5)
    d.click('[data-view="accounts"]', wait=2.4)
    for _ in range(12):
        if d.rect("#acc-school"):
            break
        d.wait(0.5)
    d.wait(0.6)
    d.type_into("#acc-school", "A大学", cps=0.08)
    d.type_into("#acc-user", "wangwu", cps=0.08)
    d.type_into("#acc-pass", "wangwu2026", cps=0.08)
    d.type_into("#acc-name", "王五", cps=0.12)
    d.wait(0.6)
    d.click("#acc-create", wait=2.4)
    d.click("#acc-tpl", wait=1.8)             # 顺手演示“下载模板”
    d.wait(1.0)


def step_map(d: Demo) -> None:
    d.say("⑦ 校园地图（这一段留给你手动录）")
    d.click('[data-view="plugin"]', wait=2.0)
    d.move_to(700, 330, pause=1.2)
    d.move_to(700, 470, pause=1.0)
    d.wait(1.5)
    if PAUSE_AT and PAUSE_AT == CURRENT[0]:
        print("\n  ⏸  停在第 7 步。你手动演示校园地图，录完按回车继续 ...", flush=True)
        input()


def step_system(d: Demo) -> None:
    d.say("⑧ 系统状态：运行参数、回收站、危险操作（只看不点）")
    d.click('[data-view="system"]', wait=2.2)
    for y in (300, 360, 420, 480, 540, 600):
        d.move_to(720, y, pause=0.42)
    d.scroll(300, 1000)
    d.wait(0.8)
    b = d.rect("#trash-list")
    if b:
        d.move_to(b["cx"], b["cy"], pause=1.2)
    d.scroll(320, 1000)
    d.wait(0.8)
    b = d.rect("#danger-rebuild")
    if b:
        d.move_to(b["cx"], b["cy"], pause=1.9)   # 只停不点
    d.wait(1.2)


def step_student_faq(d: Demo) -> None:
    d.say("⑨ 学生端：点「迎新助手」，常见问题里问新生报到时间")
    d.goto(BASE + "/student", 3.2)
    d.wait(0.8)
    picked = d.js("(()=>{const e=[...document.querySelectorAll('.entries .entry')]"
                  ".find(x=>x.textContent.includes('迎新')); "
                  "if(!e) return ''; e.click(); return e.textContent.trim().slice(0,20);})()")
    print(f"      进入助手：{picked or '(未找到迎新，接着走)'}")
    d.wait(2.4)

    hit = d.js("(()=>{const e=[...document.querySelectorAll('.faq-item')]"
               ".find(x=>/报到|新生/.test(x.textContent)); "
               "if(!e) return ''; e.click(); return e.textContent.trim().slice(0,26);})()")
    if hit:
        print(f"      点了常见问题：{hit}")
        d.wait(2.0)
        d.wait_answer()
    else:
        print("      (常见问题里没找到报到相关，跳过)")
    d.wait(1.0)


def step_student_auto(d: Demo) -> None:
    d.say("⑩ 切回自动路由，问「奖学金怎么评」，看它自己找对助手")
    d.js("(()=>{const e=[...document.querySelectorAll('.entries .entry')]"
         ".find(x=>x.textContent.includes('自动')); if(e) e.click();})()")
    d.wait(2.2)
    d.type_into("#input", "奖学金怎么评", cps=0.05)
    d.wait(0.4)
    d.enter()
    d.wait(1.5)
    d.wait_answer()
    d.click(".trace-toggle", wait=1.8)
    d.wait(2.2)


def step_github(d: Demo) -> None:
    d.say("⑪ GitHub：代码、部署文档、许可清单都开源了")
    d.goto(GITHUB, 5.5)
    d.move_to(700, 380, pause=2.0)
    d.scroll(420, 1200)
    d.wait(2.0)


STEPS = [
    (1, step_home),
    (2, step_admin_login),
    (3, step_overview_empty),
    (4, step_upload),
    (5, step_overview_full),
    (6, step_account),
    (7, step_map),
    (8, step_system),
    (9, step_student_faq),
    (10, step_student_auto),
    (11, step_github),
]


def main() -> int:
    global SLOW, PAUSE_AT
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", type=int, default=1)
    ap.add_argument("--slow", action="store_true")
    ap.add_argument("--pause-at", type=int, default=0,
                    help="走到第 N 步停住等你手动录（例：7 校园地图）")
    args = ap.parse_args()
    SLOW = 1.0 if args.slow else 0.0
    PAUSE_AT = args.pause_at

    if not net_ok():
        print("✗ 服务没起来，先双击 start.bat。")
        return 1

    # 每跑一次都换一个干净的 profile —— 否则上次的 cookie 还在，
    # 打开 /admin 会直接进去，就拍不到「输密码」那一幕了。
    import shutil as _sh, time as _t
    if os.path.isdir(PROFILE):
        for _ in range(3):
            try:
                _sh.rmtree(PROFILE, ignore_errors=True)
                if not os.path.isdir(PROFILE):
                    break
            except Exception:
                _t.sleep(0.6)
    os.makedirs(PROFILE, exist_ok=True)
    print("→ 打开 Chrome（干净会话，会真的要求输密码；录屏就录这个窗口）...")
    proc = subprocess.Popen([
        CHROME, "--no-first-run", "--no-default-browser-check",
        "--no-proxy-server", "--proxy-bypass-list=*",
        "--disable-features=Translate",
        f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE}",
        "--window-position=60,30", "--window-size=1460,970",
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
        print("✗ Chrome 起不来")
        proc.kill()
        return 1

    ws = WS(ws_url)
    for m in ("Page.enable", "Runtime.enable", "Network.enable", "DOM.enable"):
        ws.call(m)
    d = Demo(ws)

    t0 = time.time()
    try:
        # 从中间某步开始时，页面还停在空白页，先回首页把地基铺上
        if args.start > 1:
            d.goto(BASE + "/", 2.5)
        for no, fn in STEPS:
            if no < args.start:
                continue
            CURRENT[0] = no
            fn(d)
        d.hide_hint()
        print(f"\n✓ 走完，用时 {time.time()-t0:.0f} 秒")
    except KeyboardInterrupt:
        print("\n(中断)")
    finally:
        print("  窗口留着方便补录。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
