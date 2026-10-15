# -*- coding: utf-8 -*-
"""
登录与会话 —— 三类身份，权限是**包含**关系。

    super    超级管理员（.env 里那个 admin）   管理端 + 学生端，且能开号
    学校管理员（管理端开出来的 admin 类型）     管理端 + 学生端
    学生      （管理端开出来的 student 类型）   只有学生端

会话不做服务端存储：Cookie 值是 `角色.base64(账号).签发时间戳.HMAC签名`，
密钥由账号密码派生。所以改 Cookie 是没用的 —— 把 student 改成 super，
签名立刻对不上；想伪造就得先知道密码，那还不如直接登录。

账号来源有三种，按优先级：
    1. .env 里的超级管理员
    2. .env 里的内置演示学生账号
    3. data/accounts.json 里管理端开出来的账号
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Optional, Tuple

from app import accounts, config

ROLE_SUPER = "super"
ROLE_ADMIN = "admin"
ROLE_STUDENT = "student"
ROLES = (ROLE_SUPER, ROLE_ADMIN, ROLE_STUDENT)


@dataclass
class Identity:
    """一次登录所代表的人和身份。"""
    username: str
    role: str
    school: str = ""
    name: str = ""

    @property
    def is_super(self) -> bool:
        return self.role == ROLE_SUPER

    @property
    def can_admin(self) -> bool:
        """管理端：超级管理员和学校管理员都能进。"""
        return self.role in (ROLE_SUPER, ROLE_ADMIN)

    @property
    def can_student(self) -> bool:
        return self.role in ROLES

    @property
    def label(self) -> str:
        """给界面看的身份名，例如「A大学 · 张三」。"""
        if self.role == ROLE_SUPER:
            return "超级管理员"
        who = self.name or ("管理员" if self.role == ROLE_ADMIN else "学生")
        return f"{self.school} · {who}" if self.school else who

    def as_dict(self) -> dict:
        return {
            "username": self.username,
            "role": self.role,
            "school": self.school,
            "name": self.name,
            "label": self.label,
            "can_admin": self.can_admin,
            "can_student": self.can_student,
            "is_super": self.is_super,
        }


# --------------------------------------------------------------------------
# 凭据校验
# --------------------------------------------------------------------------
def check(username: str, password: str) -> Tuple[Optional[Identity], str]:
    """
    校验账号密码。返回 (身份, 错误信息)。

    先比超级管理员，再比内置学生账号，最后查动态账号表 ——
    这样即使有人把动态账号的名字设成和超级管理员一样，也抢不走超级权限
    （真正的重名拦截在 accounts.create 里）。
    """
    u = (username or "").strip()
    p = password or ""
    if not u or not p:
        return None, "请输入账号和密码"

    if u == config.ADMIN_USER and hmac.compare_digest(p, config.ADMIN_PASS):
        return Identity(username=u, role=ROLE_SUPER, name="超级管理员"), ""

    if u == config.STUDENT_USER and hmac.compare_digest(p, config.STUDENT_PASS):
        return Identity(username=u, role=ROLE_STUDENT,
                        school=config.STUDENT_SCHOOL, name="演示学生"), ""

    item = accounts.authenticate(u, p)
    if item:
        return Identity(
            username=item["username"],
            role=item.get("role") or ROLE_STUDENT,
            school=item.get("school", ""),
            name=item.get("name", ""),
        ), ""

    return None, "账号或密码不正确"


def lookup(username: str) -> Optional[Identity]:
    """
    按账号回查身份 —— 每次请求都走这里，所以管理端改学校名、
    删账号这些动作能立刻反映到已登录的会话上（删号即失效）。
    """
    u = (username or "").strip()
    if not u:
        return None
    if u == config.ADMIN_USER:
        return Identity(username=u, role=ROLE_SUPER, name="超级管理员")
    if u == config.STUDENT_USER:
        return Identity(username=u, role=ROLE_STUDENT,
                        school=config.STUDENT_SCHOOL, name="演示学生")
    item = accounts.find(u)
    if not item or item.get("enabled") is False:
        return None
    return Identity(
        username=item["username"],
        role=item.get("role") or ROLE_STUDENT,
        school=item.get("school", ""),
        name=item.get("name", ""),
    )


# --------------------------------------------------------------------------
# 会话串签发 / 校验
# --------------------------------------------------------------------------
def _secret() -> bytes:
    """签名密钥：由账号密码派生。改密码 = 所有旧会话立即作废，正合期望。"""
    raw = (f"{config.ADMIN_USER}|{config.ADMIN_PASS}|"
           f"{config.STUDENT_USER}|{config.STUDENT_PASS}")
    return hashlib.sha256(raw.encode("utf-8")).digest()


def _sign(payload: str) -> str:
    return hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _unb64(text: str) -> str:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad).decode("utf-8")


def issue(role: str, username: str) -> str:
    """签发会话串。账号走 base64，中文账号名也不会撑坏 Cookie。"""
    ts = str(int(time.time()))
    payload = f"{role}.{_b64(username)}.{ts}"
    return f"{payload}.{_sign(payload)}"


def read(raw: str) -> Optional[Tuple[str, str]]:
    """验会话串，返回 (角色, 账号)；签名不对、格式不对、过期 → None。"""
    parts = (raw or "").split(".")
    if len(parts) != 4:
        return None
    role, user_b64, ts, sig = parts
    if role not in ROLES:
        return None
    if not hmac.compare_digest(sig, _sign(f"{role}.{user_b64}.{ts}")):
        return None
    try:
        issued = int(ts)
        username = _unb64(user_b64)
    except Exception:
        return None
    if issued > time.time() + 300:      # 时钟被往前调，视为无效
        return None
    if time.time() - issued > config.SESSION_TTL:
        return None
    return role, username
