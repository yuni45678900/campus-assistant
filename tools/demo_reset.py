# -*- coding: utf-8 -*-
"""
演示前复位：把知识库清回「0 分类 / 0 文档」，原始资料搬到 演示素材\\ 备用。

为什么不是删除：
    演示的看点就是「从零开始」—— 管理端一进来 0 分类 0 文档，资料现场拖进去。
    所以 data\\raw 里那批资料不能删，只是换个地方待着，等着被现场拖回上传框。

可反复运行：
    ⚠️ 不能写成 shutil.move(src_dir, dst_dir) —— 当 dst_dir\\<同名> 已存在时，
    shutil.move 会把它移成 dst\\<名字>\\<名字> 的**嵌套结构**（经典坑）。
    所以同名分类走「逐文件合并」分支，同名文件跳过，保证幂等。

用法：
    .venv\\Scripts\\python.exe tools\\demo_reset.py             # 真复位
    .venv\\Scripts\\python.exe tools\\demo_reset.py --dry-run   # 只说要做什么，不动文件
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

# 脚本常被从任意目录启动，先把项目根塞进 sys.path，否则 import app 会失败
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config                                    # noqa: E402

# 控制台可能是 GBK，中文输出会炸；统一按 UTF-8 写、出错也不中断
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                         # noqa: BLE001
    pass

STAGE = ROOT / "演示素材"          # 演示时从这里拖文件夹进上传框
DRY = "--dry-run" in sys.argv


def _say(msg: str) -> None:
    print(("  [预演] " if DRY else "  ") + msg)


def stage_docs() -> None:
    """把 data/raw 下的分类目录与散落文件搬到 演示素材/。"""
    if not config.RAW_DIR.exists():
        _say("data/raw 不存在，跳过")
        return

    dirs = sorted(p for p in config.RAW_DIR.iterdir() if p.is_dir())
    files = sorted(p for p in config.RAW_DIR.iterdir() if p.is_file())

    if not dirs and not files:
        _say("data/raw 已经是空的 —— 无需复位")
        return

    if not DRY:
        STAGE.mkdir(exist_ok=True)

    for src in dirs:
        dst = STAGE / src.name
        n = len(list(src.rglob("*")))
        if dst.exists():
            # 暂存区已有同名分类：逐文件并回去，同名跳过（可反复跑）
            added = 0
            if not DRY:
                for f in sorted(src.rglob("*")):
                    if not f.is_file():
                        continue
                    target = dst / f.relative_to(src)
                    if not target.exists():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(f), str(target))
                        added += 1
                shutil.rmtree(src, ignore_errors=True)
            _say(f"合并「{src.name}」→ 演示素材\\{src.name}"
                 f"（原有 {n} 项，新增 {added} 项，同名跳过）")
        else:
            if not DRY:
                shutil.move(str(src), str(dst))
            _say(f"搬走「{src.name}」→ 演示素材\\{src.name}  （{n} 项）")

    for f in files:
        target = STAGE / f.name
        if target.exists():
            _say(f"跳过散件 {f.name}（演示素材\\ 里已有同名）")
            continue
        if not DRY:
            shutil.move(str(f), str(target))
        _say(f"搬走散件 {f.name} → 演示素材\\")


def clear_index() -> None:
    """清掉索引（派生数据，随时可重建）。"""
    if not config.INDEX_DIR.exists():
        _say("data/index 不存在，跳过")
        return
    items = sorted(config.INDEX_DIR.iterdir())
    if not items:
        _say("data/index 已经是空的")
        return
    for p in items:
        if not DRY:
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink()
        _say(f"清掉索引 {p.name}")
    _say("索引已清空（下次问答前需要重建索引）")


def try_reindex() -> str:
    """服务正在跑的话，顺手让它重建一次空索引；否则留给下次启动。"""
    if DRY:
        return "（预演未执行）"
    try:
        import httpx
    except ImportError:
        return "未安装 httpx，跳过；请重启服务让索引刷新"

    try:
        c = httpx.Client(base_url="http://127.0.0.1:8000", timeout=180.0,
                         trust_env=False)
        r = c.post("/api/auth/login", json={
            "username": config.ADMIN_USER,
            "password": config.ADMIN_PASS,
            "target": "admin",
        })
        if r.status_code != 200:
            return (f"服务在跑但用 {config.ADMIN_USER} 登录失败"
                    f"（HTTP {r.status_code}）—— 请手动重启服务")
        c.post("/api/admin/reindex", json={})
        d = c.get("/api/status").json().get("index", {})
        return (f"已通知服务重建索引 → 现在 {d.get('assistant_count', 0)} 分类 / "
                f"{d.get('chunk_count', 0)} 片段")
    except Exception:                                     # noqa: BLE001
        return "服务没在跑 —— 下次启动时本来就是空的"


def main() -> int:
    print()
    print("=" * 52)
    print("  校园助手 · 演示前复位" + ("（预演模式，不动任何文件）" if DRY else ""))
    print("=" * 52)
    print()
    print(" 1. 把资料搬到暂存区")
    stage_docs()
    print()
    print(" 2. 清空索引")
    clear_index()
    print()
    print(" 3. 让运行中的服务刷新索引")
    print("    " + try_reindex())
    print()

    raw_n = len(list(config.RAW_DIR.rglob("*"))) if config.RAW_DIR.exists() else 0
    stage_n = len(list(STAGE.rglob("*"))) if STAGE.exists() else 0
    print("-" * 52)
    print(f"  data/raw    剩余 {raw_n} 项（演示起点应为 0）")
    print(f"  演示素材/   共有 {stage_n} 项（演示时拖这里的文件夹）")
    print("-" * 52)
    print()
    print("  演示操作：")
    print("    打开 http://127.0.0.1:8000 → 管理端 → 上传文档")
    print("    把 演示素材\\ 下的 伴学 / 招生 / 迎新 三个文件夹分别拖进虚线框")
    print("    （拖一次多一个分类，现场看它自动归类 + 自动建索引）")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
