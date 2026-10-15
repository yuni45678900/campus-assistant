# -*- coding: utf-8 -*-
"""
账号体系 · 端到端验证（演示场景专用）。

用法（服务需已启动）：
    .venv\\Scripts\\python.exe tools\\verify_accounts.py

走的就是演示那套流程：
    管理端开号（学校 + 账号 + 密码） → 学生拿号登录学生端 → 只能进学生端
    学校管理员账号 → 管理端和学生端都能进
结束后把测试账号全部清掉，不留垃圾。
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import config as _config      # noqa: E402

BASE = "http://127.0.0.1:8000"

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond, detail: str = "") -> bool:
    RESULTS.append((name, bool(cond), detail))
    print(f"   {'OK  ' if cond else 'FAIL'} {name}"
          + (f"   {detail}" if detail else ""))
    return bool(cond)


def client(cookies: dict = None) -> httpx.Client:
    return httpx.Client(base_url=BASE, timeout=30.0, trust_env=False,
                        cookies=cookies or {})


def login(username: str, password: str, target: str = "student"):
    c = client()
    r = c.post("/api/auth/login",
               json={"username": username, "password": password, "target": target})
    return c, r


TEST_SCHOOL = "演示大学"
TEST_USER = "demo2026"
TEST_PASS = "demo123"
TEST_ADMIN_USER = "demo-admin"
TEST_ADMIN_PASS = "demo123"


def main() -> int:
    print("=" * 56)
    print("  账号体系 · 端到端验证")
    print("=" * 56)

    admin = None
    try:
        # ── 0. 超级管理员 ──
        print("\n── 超级管理员 ──")
        admin, r = login(_config.ADMIN_USER, _config.ADMIN_PASS, "admin")
        d = r.json()
        check("超级管理员登录管理端", r.status_code == 200 and d.get("role") == "super",
              f"HTTP {r.status_code} role={d.get('role')}")
        check("超级管理员能进管理端", admin.get("/admin").status_code == 200)
        check("超级管理员能进学生端", admin.get("/student").status_code == 200)

        # ── 1. 管理端开号（演示主流程）──
        print("\n── 管理端开号 ──")
        print(f"   学校={TEST_SCHOOL}  账号={TEST_USER}  密码={TEST_PASS}")
        r = admin.post("/api/admin/accounts", json={
            "username": TEST_USER, "password": TEST_PASS,
            "school": TEST_SCHOOL, "name": "演示同学", "role": "student",
        })
        check("开学生账号", r.status_code == 200 and r.json().get("ok"),
              f"HTTP {r.status_code} {r.json().get('error', '')}")

        r = admin.post("/api/admin/accounts", json={
            "username": TEST_USER, "password": "x", "school": TEST_SCHOOL,
        })
        check("重名账号被拒 400", r.status_code == 400, r.json().get("error", ""))

        r = admin.post("/api/admin/accounts", json={
            "username": "ab", "password": "x", "school": TEST_SCHOOL,
        })
        check("非法账号名被拒 400", r.status_code == 400, r.json().get("error", ""))

        r = admin.post("/api/admin/accounts", json={
            "username": _config.ADMIN_USER, "password": "x", "school": TEST_SCHOOL,
        })
        check("冒用超级管理员名被拒 400", r.status_code == 400, r.json().get("error", ""))

        r = admin.get("/api/admin/accounts")
        names = [a["username"] for a in r.json().get("accounts", [])]
        check("账号出现在列表里", TEST_USER in names, f"{len(names)} 条")

        # ── 2. 学生拿号登录 ──
        print("\n── 学生拿号登录 ──")
        stu, r = login(TEST_USER, TEST_PASS, "student")
        d = r.json()
        check("新账号可登录学生端", r.status_code == 200 and d.get("ok"),
              f"HTTP {r.status_code}")
        check("会话带回学校名", d.get("school") == TEST_SCHOOL, f"school={d.get('school')}")
        check("身份标签含学校与姓名", TEST_SCHOOL in (d.get("label") or ""),
              d.get("label", ""))
        check("学生可进 /student", stu.get("/student").status_code == 200)
        check("学生进不了 /admin", stu.get("/admin", follow_redirects=False).status_code == 302)
        check("学生调管理接口 403", stu.get("/api/admin/overview").status_code == 403)
        check("学生调管理接口文案明确",
              "学生账号" in stu.get("/api/admin/overview").json().get("error", ""))

        tmp, r = login(TEST_USER, TEST_PASS, "admin")
        tmp.close()
        check("学生登管理端被拒 403", r.status_code == 403, r.json().get("error", ""))

        tmp, r = login(TEST_USER, "wrong-pass", "student")
        tmp.close()
        check("密码错误 401", r.status_code == 401)

        # ── 3. 学校管理员账号 ──
        print("\n── 学校管理员账号 ──")
        r = admin.post("/api/admin/accounts", json={
            "username": TEST_ADMIN_USER, "password": TEST_ADMIN_PASS,
            "school": TEST_SCHOOL, "name": "校方老师", "role": "admin",
        })
        check("开学校管理员账号", r.status_code == 200, r.json().get("error", ""))

        sa, r = login(TEST_ADMIN_USER, TEST_ADMIN_PASS, "admin")
        check("学校管理员能登管理端", r.status_code == 200 and r.json().get("can_admin"),
              f"HTTP {r.status_code}")
        check("学校管理员能进 /admin", sa.get("/admin").status_code == 200)
        check("学校管理员能进 /student", sa.get("/student").status_code == 200)
        check("学校管理员可调管理接口",
              sa.get("/api/admin/overview").status_code == 200)
        sa.close()

        # ── 4. 管理端改学校名 / 删号即下线 ──
        print("\n── 改名与删号 ──")
        r = stu.post("/api/auth/school", json={"username": TEST_USER, "school": "演示大学（新）"})
        check("补填/修改学校名", r.status_code == 200, r.json().get("error", ""))
        check("改完立刻生效",
              stu.get("/api/auth/session").json().get("school") == "演示大学（新）")

        r = stu.post("/api/auth/school", json={"username": "别人的号", "school": "X"})
        check("只能改自己的学校名 403", r.status_code == 403)

        r = admin.delete("/api/admin/accounts", params={"username": TEST_USER})
        check("删除账号", r.status_code == 200 and r.json().get("ok"))
        check("删号后旧会话立即失效", stu.get("/student", follow_redirects=False).status_code == 302)

        r = admin.delete("/api/admin/accounts", params={"username": "根本没有的号"})
        check("删不存在的账号 400", r.status_code == 400)

        # ── 5. 学生越权访问账号接口 ──
        print("\n── 越权防护 ──")
        tmp, r = login(_config.STUDENT_USER, _config.STUDENT_PASS, "student")
        check("学生调账号列表 403", tmp.get("/api/admin/accounts").status_code == 403)
        check("学生开号 403",
              tmp.post("/api/admin/accounts", json={
                  "username": "hacker01", "password": "x", "school": "野鸡大学"
              }).status_code == 403)
        tmp.close()

    except httpx.ConnectError:
        print("\n无法连接服务，请先启动：start.bat 或 python -m app.server")
        return 2
    finally:
        # ── 清理：把本次测试开的号全删掉 ──
        print("\n── 清理测试账号 ──")
        if admin is not None:
            for u in (TEST_USER, TEST_ADMIN_USER):
                r = admin.delete("/api/admin/accounts", params={"username": u})
                print(f"   删除 {u} -> HTTP {r.status_code}")
            left = [a["username"] for a in admin.get("/api/admin/accounts").json()["accounts"]
                    if not a.get("builtin")]
            print(f"   剩余自建账号：{left or '无'}")
            admin.close()

    ok = sum(1 for _, c, _ in RESULTS if c)
    fail = [r for r in RESULTS if not r[1]]
    print("\n" + "=" * 56)
    print(f"  通过 {ok} / {len(RESULTS)} 项")
    if fail:
        print("\n  未通过：")
        for n, _, d in fail:
            print(f"    · {n}  {d}")
    print("=" * 56)
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
