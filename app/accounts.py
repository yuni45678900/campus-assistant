# -*- coding: utf-8 -*-
"""
演示用的动态账号表 —— 管理端现场开号，学生端拿号登录。

两套账号来源分工清晰：

    .env（CAMPUS_ADMIN_USER/PASS）  →  超级管理员，固定不变，永远能进管理端
    data/accounts.json             →  管理端开出来的账号（学生 / 学校管理员）

每个动态账号都带一个**学校名**：开号时写清楚是哪所大学的，
学生用它登录后，学生端顶部就显示这所大学 —— 演示时一眼能看出"这是给谁开的号"。

⚠ 定位是演示：密码明文存在本地 JSON 里，够用且好排查（忘了密码直接打开看）。
   真上生产应当换成哈希 + 数据库，这一点在 README 里也写明了。
"""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Dict, List, Optional, Tuple

from app import config

# 用户名/学校名的长度与字符约束（账号要能安全地塞进 URL、Cookie）
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{3,24}$")
MAX_SCHOOL_LEN = 40
MAX_NAME_LEN = 20
MAX_PASSWORD_LEN = 32

_LOCK = threading.Lock()

ROLE_STUDENT = "student"        # 只能进学生端
ROLE_ADMIN = "admin"            # 学校管理员：管理端 + 学生端

ROLE_LABEL = {ROLE_STUDENT: "学生", ROLE_ADMIN: "学校管理员"}


# --------------------------------------------------------------------------
# 存取
# --------------------------------------------------------------------------
def _read() -> List[dict]:
    f = config.ACCOUNTS_FILE
    if not f.exists():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[accounts] 读取失败，按空表处理：{e}")
        return []
    items = data.get("accounts") if isinstance(data, dict) else data
    return [x for x in (items or []) if isinstance(x, dict)]


def _write(items: List[dict]) -> None:
    f = config.ACCOUNTS_FILE
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"accounts": items}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(f)          # 原子替换：写一半被打断也不会留下坏文件


# --------------------------------------------------------------------------
# 查询
# --------------------------------------------------------------------------
def list_all() -> List[dict]:
    """按创建时间倒序返回全部账号（含密码 —— 管理端要能把它念给学生）。"""
    return sorted(_read(), key=lambda x: x.get("created_at", ""), reverse=True)


def find(username: str) -> Optional[dict]:
    u = (username or "").strip()
    for it in _read():
        if it.get("username") == u:
            return it
    return None


def authenticate(username: str, password: str) -> Optional[dict]:
    """账号密码校验，成功返回账号记录。"""
    it = find(username)
    if not it:
        return None
    if it.get("enabled") is False:
        return None
    if (password or "") != it.get("password", ""):
        return None
    return it


# --------------------------------------------------------------------------
# 增删改
# --------------------------------------------------------------------------
def _clean(text: str, limit: int) -> str:
    return (text or "").strip()[:limit]


def create(username: str, password: str, school: str = "", name: str = "",
           role: str = ROLE_STUDENT) -> Tuple[Optional[dict], str]:
    """开一个新账号。返回 (记录, 错误信息)；成功时错误信息为空串。"""
    u = (username or "").strip()
    p = password or ""
    school = _clean(school, MAX_SCHOOL_LEN)
    name = _clean(name, MAX_NAME_LEN)
    role = role if role in (ROLE_STUDENT, ROLE_ADMIN) else ROLE_STUDENT

    if not u or not p:
        return None, "账号和密码都不能为空"
    if not USERNAME_RE.match(u):
        return None, "账号需 3~24 位，只能用字母、数字、下划线、点或横线"
    if len(p) > MAX_PASSWORD_LEN:
        return None, f"密码最长 {MAX_PASSWORD_LEN} 位"
    if u == config.ADMIN_USER:
        return None, f"「{u}」是超级管理员账号，请换一个"

    with _LOCK:
        items = _read()
        if any(x.get("username") == u for x in items):
            return None, f"账号「{u}」已存在"

        item = {
            "username": u,
            "password": p,
            "school": school,
            "name": name,
            "role": role,
            "enabled": True,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        items.append(item)
        _write(items)
    return item, ""


def create_many(rows: List[dict], default_school: str = "",
                allow_admin: bool = False) -> dict:
    """批量开号：一次读盘、一次写盘，避免逐条创建时的重复 IO。

    rows 每项形如 {"username","password","school","name","role"}。
    返回 {"created": [...], "skipped": [...], "failed": [...]}，
    三条列表里都是 {"username": ..., "reason": ...} 或 {"username": ..., "account": ...}。
    """
    created: List[dict] = []
    skipped: List[dict] = []
    failed: List[dict] = []
    default_school = _clean(default_school, MAX_SCHOOL_LEN)

    with _LOCK:
        items = _read()
        existing = {x.get("username") for x in items}

        for row in rows:
            u = (row.get("username") or "").strip()
            pw = row.get("password") or ""
            school = _clean(row.get("school") or default_school, MAX_SCHOOL_LEN)
            name = _clean(row.get("name"), MAX_NAME_LEN)
            role = (row.get("role") or "").strip()
            role = ROLE_ADMIN if role in ("管理员", "学校管理员", "admin", "管理员老师") else ROLE_STUDENT

            # 逐行校验，错一行不影响其他行
            if not u or not pw:
                failed.append({"username": u, "reason": "账号或密码为空"})
                continue
            if not USERNAME_RE.match(u):
                failed.append({"username": u, "reason": "账号格式不合法"})
                continue
            if len(pw) > MAX_PASSWORD_LEN:
                failed.append({"username": u, "reason": "密码过长"})
                continue
            if u == config.ADMIN_USER:
                failed.append({"username": u, "reason": "与超级管理员账号重名"})
                continue
            if role == ROLE_ADMIN and not allow_admin:
                failed.append({"username": u, "reason": "只有超级管理员能开管理员账号"})
                continue
            if u in existing:
                skipped.append({"username": u, "reason": "账号已存在"})
                continue

            item = {
                "username": u,
                "password": pw,
                "school": school,
                "name": name,
                "role": role,
                "enabled": True,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            items.append(item)
            existing.add(u)
            created.append({"username": u, "account": item})

        if created:
            _write(items)

    return {"created": created, "skipped": skipped, "failed": failed}


def remove(username: str) -> Tuple[bool, str]:
    """删账号。admin 是 .env 里的超级管理员，不在这张表里，删不到。"""
    u = (username or "").strip()
    with _LOCK:
        items = _read()
        rest = [x for x in items if x.get("username") != u]
        if len(rest) == len(items):
            return False, f"账号「{u}」不存在"
        _write(rest)
    return True, ""


def update_school(username: str, school: str) -> Tuple[bool, str]:
    """
    改账号的学校名 —— 给"学生端自己填"留的口子：
    管理端开号时没写学校，学生可以自己补上。
    """
    school = _clean(school, MAX_SCHOOL_LEN)
    if not school:
        return False, "学校名不能为空"
    with _LOCK:
        items = _read()
        for it in items:
            if it.get("username") == username:
                it["school"] = school
                _write(items)
                return True, ""
    return False, "账号不存在"


def stats() -> Dict[str, int]:
    items = _read()
    return {
        "total": len(items),
        "students": sum(1 for x in items if x.get("role") == ROLE_STUDENT),
        "admins": sum(1 for x in items if x.get("role") == ROLE_ADMIN),
    }
