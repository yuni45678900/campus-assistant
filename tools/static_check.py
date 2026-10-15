# -*- coding: utf-8 -*-
"""
前端静态自检：内联脚本语法 / 元素 id 引用 / 标签配平。

用法（项目根目录下）：
    .venv\\Scripts\\python.exe tools\\static_check.py

三份页面都是"单文件、零外部依赖"，所有 JS 内联在 <script> 里，
没有构建步骤，所以这类静态检查就是最划算的质量闸门：
语法错、id 拼错、标签没闭合 —— 打开页面前就能发现。

需要 Node.js 做 JS 语法检查（node --check）；找不到则跳过该项。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
PAGES = ("index.html", "student.html", "admin.html")
TAGS = ("div", "span", "table", "tbody", "thead", "tr", "td", "th", "button",
        "aside", "main", "select", "textarea", "label", "h1", "h2", "a",
        "style", "script", "svg", "p")
# 管理端才有这些多视图容器
# 管理端的视图 id（nav-item 的 data-view 值）。改了侧栏记得同步这里。
VIEW_IDS = ("overview", "kb", "upload", "accounts", "system")


def find_node() -> str | None:
    for name in ("node", "node.exe"):
        p = shutil.which(name)
        if p:
            return p
    for cand in (
        r"C:\Users\20483\.workbuddy\binaries\node\versions\22.22.2-3\node.exe",
        r"D:\nodejs\node.exe",
    ):
        if Path(cand).exists():
            return cand
    return None


def main() -> int:
    node = find_node()
    print("=" * 52)
    print("  前端静态自检")
    print("=" * 52)
    if not node:
        print("  [提示] 未找到 node，跳过 JS 语法检查\n")
    bad = 0

    with tempfile.TemporaryDirectory() as td:
        for fname in PAGES:
            p = WEB / fname
            if not p.exists():
                print(f"\n[缺失] {fname}")
                bad += 1
                continue
            html = p.read_text(encoding="utf-8")
            print(f"\n== {fname}  ({len(html) / 1024:.1f} KB, "
                  f"{html.count(chr(10)) + 1} 行)")

            # 1) 内联脚本语法
            for i, s in enumerate(re.findall(r"<script[^>]*>(.*?)</script>", html, re.S)):
                if not s.strip() or not node:
                    continue
                tmp = Path(td) / f"{fname}_{i}.js"
                tmp.write_text(s, encoding="utf-8")
                r = subprocess.run([node, "--check", str(tmp)],
                                   capture_output=True, text=True)
                ok = r.returncode == 0
                print(f"   [脚本 {i}] {'OK  语法通过' if ok else 'FAIL 语法错误'}")
                if not ok:
                    print("      " + (r.stderr or "")[:600].replace("\n", "\n      "))
                    bad += 1

            # 2) id 引用
            ids = set(re.findall(r'\bid="([^"]+)"', html))
            refs = set(re.findall(r'\$\("([^"]+)"\)', html))
            refs |= set(re.findall(r'getElementById\("([^"]+)"\)', html))
            missing = sorted(x for x in refs if x not in ids and not x.startswith("v-"))
            dyn_ok = True
            if fname == "admin.html":
                dyn_ok = all(("v-" + v) in ids for v in VIEW_IDS)
            print(f"   [id] 定义 {len(ids)} / 引用 {len(refs)} → 缺失："
                  f"{missing if missing else '无'}"
                  f"（动态 v-* ：{'完整' if dyn_ok else '缺'}）")
            if missing or not dyn_ok:
                bad += 1

            # 3) 标签配平
            unbalanced = []
            for tag in TAGS:
                o = len(re.findall(rf"<{tag}[\s>]", html))
                c = len(re.findall(rf"</{tag}>", html))
                if o != c:
                    unbalanced.append(f"<{tag}> 开{o}/闭{c}")
            print(f"   [标签] {'OK  全部配平' if not unbalanced else 'FAIL ' + '，'.join(unbalanced)}")
            if unbalanced:
                bad += 1

    print("\n" + "=" * 52)
    print("  结论：" + ("全部通过" if bad == 0 else f"{bad} 项待处理"))
    print("=" * 52)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
