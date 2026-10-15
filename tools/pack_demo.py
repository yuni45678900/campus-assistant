# -*- coding: utf-8 -*-
"""把 demo-site 打包成可导入资料库的 zip（入口 index.html 位于压缩包根目录）。

用法：.venv\\Scripts\\python.exe tools/pack_demo.py [输出路径]
"""
from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "demo-site"
SKIP = {"qa.json"}   # 构建中间产物，不进包


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "campus-demo.zip"
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for cur, _dirs, files in os.walk(BASE):
            for f in files:
                p = Path(cur) / f
                rel = p.relative_to(BASE).as_posix()
                if rel in SKIP:
                    continue
                z.write(p, rel)
                n += 1
    print(f"✓ {out}  （{n} 个文件，{out.stat().st_size/1024:.0f} KB）")
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        print("  入口:", [x for x in names if x.endswith((".html", ".htm"))])
        print("  资源:", [x for x in names if not x.endswith((".html", ".htm"))])
    return 0


if __name__ == "__main__":
    sys.exit(main())
