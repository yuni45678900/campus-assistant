# -*- coding: utf-8 -*-
"""
问答记录（管理端「问答记录」的数据底座）。

学生端每问一轮，就在 data/logs/qa.jsonl 追加一条：
    谁问的 / 什么时候 / 问的什么 / 答的什么 / 耗时多久 / 命中哪些资料 / 有没有答不出来。

为什么用 jsonl 而不是数据库：
    这是个要打包分发、在别人电脑上双击就跑的演示系统，多一个 sqlite 文件就多一层
    依赖和迁移的麻烦。jsonl 一行一条、纯追加、坏一行只丢一行，用记事本打开也能看。
    管理端那页读它，导出也是它，删记录直接删文件即可。

性能上的取舍：
    · 写：只在 append 时加一次文件锁，不读全文件，不影响问答响应；
    · 读：一次性读进来（文件上限 8 MB，约两万条），在内存里筛选分页。
      演示场景完全够；真要到几十万条再换 sqlite 也不迟。
"""
from __future__ import annotations

import csv
import io
import json
import threading
import time
from typing import Dict, List, Optional

from app import config

# 单文件上限：超过就砍掉最老的一半，避免长期运行把磁盘写满
MAX_BYTES = 8 * 1024 * 1024
KEEP_ON_TRIM = 4000          # 触发瘦身后保留的条数
MAX_ANSWER = 4000            # 单条答案最多存这么多字（超长回答截断，避免撑爆文件）

_lock = threading.Lock()


def _file():
    return config.LOG_DIR / "qa.jsonl"


# --------------------------------------------------------------------------
# 写
# --------------------------------------------------------------------------
def _now_text(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _normalize(rec: Dict) -> Dict:
    """把一条记录规整成固定字段，缺的补默认值 —— 前端渲染时不用到处判空。"""
    ts = float(rec.get("ts") or time.time())
    ans = str(rec.get("answer") or "")
    if len(ans) > MAX_ANSWER:
        ans = ans[:MAX_ANSWER] + "……（过长已截断）"
    srcs = rec.get("sources") or []
    if not isinstance(srcs, list):
        srcs = []
    # 命中的文档去重、限量，只留文件名（路径没意义还占地方）
    seen, uniq = set(), []
    for s in srcs:
        s = str(s).replace("\\", "/").split("/")[-1]
        if s and s not in seen:
            seen.add(s)
            uniq.append(s)
        if len(uniq) >= 6:
            break
    return {
        "ts": round(ts, 2),
        "time": rec.get("time") or _now_text(ts),
        "user": str(rec.get("user") or ""),
        "name": str(rec.get("name") or ""),
        "school": str(rec.get("school") or ""),
        "role": str(rec.get("role") or ""),
        "assistant": str(rec.get("assistant") or "自动路由"),
        "question": str(rec.get("question") or "")[:500],
        "answer": ans,
        "elapsed": float(rec.get("elapsed") or 0),
        "chars": int(rec.get("chars") or len(ans)),
        "no_hit": bool(rec.get("no_hit")),
        "interrupted": bool(rec.get("interrupted")),
        "sources": uniq,
    }


def add(rec: Dict) -> None:
    """追加一条记录。任何异常都吞掉 —— 记录失败绝不能影响学生端问答。"""
    try:
        row = _normalize(rec)
    except Exception:
        return
    try:
        with _lock:
            f = _file()
            f.parent.mkdir(parents=True, exist_ok=True)
            with f.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            try:
                if f.stat().st_size > MAX_BYTES:
                    _trim(f)
            except OSError:
                pass
    except Exception:
        pass


def _trim(f) -> None:
    """文件过大：只留最后 KEEP_ON_TRIM 条。先写临时文件再替换，别写坏原件。"""
    rows = _read_rows(f)
    if len(rows) <= KEEP_ON_TRIM:
        return
    tmp = f.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for r in rows[-KEEP_ON_TRIM:]:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(f)


# --------------------------------------------------------------------------
# 读
# --------------------------------------------------------------------------
def _read_rows(f=None) -> List[Dict]:
    f = f or _file()
    if not f.exists():
        return []
    out: List[Dict] = []
    try:
        with f.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue          # 半行 / 坏行：跳过，别让一条脏数据毁掉整页
                if isinstance(d, dict):
                    out.append(d)
    except OSError:
        return []
    return out


def _today_prefix() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def scope(rows: List[Dict], school: Optional[str]) -> List[Dict]:
    """按学校收窄可见范围。

    school = None  → 不限（超级管理员 / 旁路口令：看全部）；
    school = "某校" → 只看这个学校的记录。

    这是「学校管理员只看本校」的落点 —— 它在数据层做，不在前端做，
    所以就算绕过页面直接打接口，也拿不到别校的记录。
    """
    if school is None:
        return rows
    return [r for r in rows if (r.get("school") or "") == school]


def summary(rows: Optional[List[Dict]] = None,
            school: Optional[str] = None) -> Dict:
    """概览用的小统计：总数、今日、未命中、按助手分布。"""
    rows = _read_rows() if rows is None else rows
    rows = scope(rows, school)
    today = _today_prefix()
    per: Dict[str, int] = {}
    for r in rows:
        a = r.get("assistant") or "自动路由"
        per[a] = per.get(a, 0) + 1
    return {
        "total": len(rows),
        "today": sum(1 for r in rows if str(r.get("time", "")).startswith(today)),
        "no_hit": sum(1 for r in rows if r.get("no_hit")),
        "per_assistant": dict(sorted(per.items(), key=lambda kv: -kv[1])),
    }


def query(limit: int = 50, offset: int = 0, assistant: str = "",
          keyword: str = "", miss_only: bool = False,
          school: Optional[str] = None) -> Dict:
    """倒序分页查记录。筛选在内存里做，返回当前页 + 命中总数 + 统计。

    school 见 `scope()`：None = 全部，字符串 = 只看这个学校。
    """
    rows = scope(_read_rows(), school)

    if assistant:
        rows = [r for r in rows if (r.get("assistant") or "") == assistant]
    if miss_only:
        rows = [r for r in rows if r.get("no_hit") or r.get("interrupted")]
    if keyword:
        k = keyword.strip().lower()
        rows = [r for r in rows
                if k in str(r.get("question", "")).lower()
                or k in str(r.get("answer", "")).lower()
                or k in str(r.get("user", "")).lower()
                or k in str(r.get("school", "")).lower()]

    rows.reverse()                      # 最新的排最前
    total = len(rows)
    limit = max(1, min(int(limit or 50), 500))
    offset = max(0, int(offset or 0))
    return {
        "ok": True,
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": rows[offset:offset + limit],
        "summary": summary(rows),
        "scope": school,                # None = 全部；前端据此显示"仅本校"
    }


def clear(school: Optional[str] = None) -> int:
    """删除记录，返回删掉的条数。

    school=None 清空全部；给了学校就**只删这个学校的**，别的学校的行原样保留 ——
    学校管理员点「清空」不该把兄弟学校的记录一起抹掉。
    """
    with _lock:
        rows = _read_rows()
        if school is None:
            kept, removed = [], len(rows)
        else:
            kept = [r for r in rows if (r.get("school") or "") != school]
            removed = len(rows) - len(kept)
        if not removed:
            return 0
        f = _file()
        tmp = f.with_suffix(".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as fh:
                for r in kept:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            tmp.replace(f)
        except OSError:
            return 0
    return removed


def assistants(school: Optional[str] = None) -> List[str]:
    """出现过哪些助手 —— 给筛选下拉用（比从索引取更真实：只有被问过才列）。

    也要按学校收窄：学校管理员的下拉里不该出现别校才问过的助手。
    """
    per: Dict[str, int] = {}
    for r in scope(_read_rows(), school):
        a = r.get("assistant") or "自动路由"
        per[a] = per.get(a, 0) + 1
    return [k for k, _ in sorted(per.items(), key=lambda kv: -kv[1])]


def export_rows(school: Optional[str] = None) -> List[Dict]:
    """导出用：记录按时间正序（老的在前，像流水账）。school=None 导全部。"""
    return scope(_read_rows(), school)


CSV_COLUMNS = ["时间", "账号", "姓名", "学校", "身份", "助手", "问题",
               "回答", "耗时(秒)", "字数", "命中资料", "结果"]


def to_csv(rows: List[Dict]) -> str:
    """转成 CSV 文本。

    开头加 UTF-8 BOM：不加的话 Excel 会按 GBK 解，中文全变乱码 ——
    这是中文用户拿到 CSV 后最先遇到的坑。
    """
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(CSV_COLUMNS)
    for r in rows:
        if r.get("interrupted"):
            state = "中断"
        elif r.get("no_hit"):
            state = "未命中"
        else:
            state = "正常"
        w.writerow([
            r.get("time", ""), r.get("user", ""), r.get("name", ""),
            r.get("school", ""), r.get("role", ""), r.get("assistant", ""),
            r.get("question", ""), r.get("answer", ""),
            r.get("elapsed", ""), r.get("chars", ""),
            "、".join(r.get("sources") or []), state,
        ])
    return "\ufeff" + buf.getvalue()
