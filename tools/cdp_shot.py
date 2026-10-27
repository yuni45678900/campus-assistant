# -*- coding: utf-8 -*-
"""用 CDP 控制 Chrome：登录后截图学生端 / 管理端。

不依赖 playwright/selenium —— 手写最小 WebSocket 客户端（RFC6455 文本帧）。
"""

import base64
import json
import os
import socket
import struct
import subprocess
import sys
import time
import urllib.request

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9222
OUT_DIR = r"D:\campus-assistant\docs\shots2"


class WS:
    """最小 WebSocket 客户端，只处理文本帧。"""

    def __init__(self, url: str):
        # ws://127.0.0.1:9222/devtools/page/XXXX
        rest = url.split("://", 1)[1]
        hostport, path = rest.split("/", 1)
        host, port = hostport.split(":")
        self.sock = socket.create_connection((host, int(port)), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET /{path} HTTP/1.1\r\n"
            f"Host: {hostport}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)
        if b"101" not in buf.split(b"\r\n")[0]:
            raise RuntimeError("WebSocket 握手失败: " + buf[:200].decode("latin1"))
        self.buf = b""

    def send(self, obj):
        data = json.dumps(obj).encode()
        header = bytearray([0x81])
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(bytes(header) + masked)

    def _read_exact(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("连接关闭")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv(self):
        while True:
            b1, b2 = self._read_exact(2)
            opcode = b1 & 0x0F
            length = b2 & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read_exact(8))[0]
            payload = self._read_exact(length) if length else b""
            if opcode == 0x8:
                raise RuntimeError("服务端关闭连接")
            if opcode in (0x1, 0x2):
                return json.loads(payload.decode("utf-8", "replace"))
            # ping/pong 等忽略

    def call(self, method, params=None, timeout=30):
        self.send({"id": self.next_id, "method": method, "params": params or {}})
        want = self.next_id
        self.next_id += 1
        t0 = time.time()
        while time.time() - t0 < timeout:
            msg = self.recv()
            if msg.get("id") == want:
                if "error" in msg:
                    raise RuntimeError(f"{method} 出错: {msg['error']}")
                return msg.get("result", {})
        raise TimeoutError(method)

    next_id = 1


def http_json(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read())


def shot(ws, url, out_name, wait=2.0):
    ws.call("Page.navigate", {"url": url})
    time.sleep(wait)
    r = ws.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
    path = os.path.join(OUT_DIR, out_name)
    with open(path, "wb") as f:
        f.write(base64.b64decode(r["data"]))
    print(f"  {out_name}: {os.path.getsize(path)//1024} KB")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    profile = r"D:\campus-assistant\.workbuddy\tmp\cdp-profile"
    proc = subprocess.Popen([
        CHROME, "--headless=new", "--no-sandbox", "--no-proxy-server",
        "--proxy-bypass-list=*", "--disable-gpu", "--hide-scrollbars",
        f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}",
        "--window-size=1440,900", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    ws_url = None
    for _ in range(30):
        time.sleep(0.5)
        try:
            tabs = http_json(f"http://127.0.0.1:{PORT}/json/list")
            pages = [t for t in tabs if t.get("type") == "page"]
            if pages:
                ws_url = pages[0]["webSocketDebuggerUrl"]
                break
        except Exception:
            continue
    if not ws_url:
        print("拿不到调试端口"); proc.kill(); return 1
    print("CDP 已连接")

    ws = WS(ws_url)
    ws.call("Page.enable")
    ws.call("Runtime.enable")
    ws.call("Emulation.setDeviceMetricsOverride",
            {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})

    # 1) 学生端：先登录，再截图
    ws.call("Page.navigate", {"url": "http://127.0.0.1:8000/student"})
    time.sleep(2.5)
    for user in ("a-student", "a-admin"):
        js = f"""async () => {{
            const r = await fetch('/api/auth/login', {{
                method: 'POST', headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{username: '{user}', password: '123456'}})
            }});
            const d = await r.json();
            return JSON.stringify({{ok: r.ok, status: r.status, d: d}});
        }}"""
        res = ws.call("Runtime.evaluate",
                      {"expression": js, "awaitPromise": True, "returnByValue": True})
        val = res.get("result", {}).get("value", "")
        print(f"  登录 {user}: {str(val)[:120]}")

        if user == "a-student":
            ws.call("Page.reload")
            time.sleep(3)
            shot(ws, "http://127.0.0.1:8000/student", "05_学生端_登录后.png", wait=3.0)
        else:
            ws.call("Page.reload")
            time.sleep(3)
            shot(ws, "http://127.0.0.1:8000/admin", "06_管理端_登录后.png", wait=4.0)

    proc.kill()
    print("完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
