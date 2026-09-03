"""Step 4 用户管理健壮性测试：注册/登录/登出边界、角色变更、列表分页、重置。

基础流程见 test_auth.py；本文件覆盖 api.md 的边界语义：
- 注册：用户名 3–40 字符、密码 ≥6、重名 400、并发注册唯一约束兜底
- 登录：精确响应、错误凭据 401、封禁 403、会话过期/吊销
- 权限变更：操作日志、授予管理员、封禁后旧会话立即失效
- 用户列表：分页语义与提交记录一致（page 单独出现 → 400；非法值 → 400）
- reset：清空全部数据、旧会话失效、恢复初始环境
"""
import asyncio
from datetime import datetime, timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.deps import SESSION_COOKIE
from app.database import SessionLocal
from app.main import app
from app.models import RoleChangeLog, Session, Submission
from conftest import login

USER_KEYS = {"user_id", "username", "join_time", "role", "submit_count", "resolve_count"}


def _new_client() -> AsyncClient:
    """独立 Cookie 的第二个客户端（模拟另一浏览器会话）。"""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _reg(client, username, password):
    return await client.post("/api/users/", json={"username": username, "password": password})


async def _get_id(client, username):
    resp = await client.get("/api/users/")
    users = resp.json()["data"]["users"]
    return next(u["user_id"] for u in users if u["username"] == username)


# ---- 注册 ----


async def test_register_response_shape(client):
    resp = await _reg(client, "alice", "secret1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200
    assert body["msg"] == "register success"
    assert set(body["data"]) == USER_KEYS          # 不返回密码
    data = body["data"]
    assert data["user_id"] == str(data["user_id"])  # user_id 为字符串（api.md）
    assert data["role"] == "user"
    assert data["submit_count"] == 0
    assert data["resolve_count"] == 0
    assert datetime.strptime(data["join_time"], "%Y-%m-%d")


async def test_register_username_boundaries(client):
    for name in ("ab", "x" * 41):                  # 2 / 41 字符 → 400
        assert (await _reg(client, name, "secret1")).status_code == 400
    assert (await _reg(client, "abc", "secret1")).status_code == 200           # 3 字符
    assert (await _reg(client, "u" * 40, "secret1")).status_code == 200        # 40 字符


async def test_register_password_boundaries(client):
    assert (await _reg(client, "bob", "12345")).status_code == 400             # 5 位
    assert (await _reg(client, "bob", "123456")).status_code == 200            # 6 位


async def test_register_missing_fields(client):
    assert (await client.post("/api/users/", json={})).status_code == 400
    assert (await client.post("/api/users/", json={"username": "x" * 3})).status_code == 400
    assert (await client.post("/api/users/", json={"password": "secret1"})).status_code == 400


async def test_register_duplicate_and_case(client):
    assert (await _reg(client, "bob", "secret1")).status_code == 200
    resp = await _reg(client, "bob", "secret1")
    assert resp.status_code == 400
    assert resp.json()["msg"] == "username already exists"
    # 用户名区分大小写
    assert (await _reg(client, "Bob", "secret1")).status_code == 200


async def test_register_concurrent_same_name(client):
    # 并发注册同一用户名：唯一约束兜底 → 恰好 1 个 200，其余 400（不能 500）
    rs = await asyncio.gather(*[_reg(client, "race", "secret1") for _ in range(5)])
    assert sorted(r.status_code for r in rs) == [200, 400, 400, 400, 400]


async def test_register_concurrent_distinct_names(client):
    rs = await asyncio.gather(*[_reg(client, f"user{i}", "secret1") for i in range(5)])
    assert sorted(r.status_code for r in rs) == [200] * 5


# ---- 登录 / 登出 ----


async def test_login_response_shape(client):
    await _reg(client, "alice", "secret1")
    resp = await client.post("/api/auth/login", json={"username": "alice", "password": "secret1"})
    assert resp.status_code == 200
    body = resp.json()
    # api.md 示例：{"code": 200, "msg": "login success", "data": {"user_id": "1", ...}}
    assert body == {
        "code": 200,
        "msg": "login success",
        "data": {"user_id": body["data"]["user_id"], "username": "alice", "role": "user"},
    }
    assert body["data"]["user_id"] == str(body["data"]["user_id"])


async def test_login_wrong_credentials(client):
    await _reg(client, "alice", "secret1")
    for payload in (
        {"username": "alice", "password": "wrong1"},          # 密码错
        {"username": "nobody", "password": "secret1"},        # 用户不存在
        {"username": "ab", "password": "secret1"},            # 登录不校验用户名长度 → 401 而非 400
    ):
        resp = await client.post("/api/auth/login", json=payload)
        assert resp.status_code == 401
        assert resp.json()["msg"] == "wrong username or password"


async def test_login_missing_fields(client):
    assert (await client.post("/api/auth/login", json={})).status_code == 400
    assert (await client.post("/api/auth/login", json={"username": "a" * 3})).status_code == 400


async def test_login_banned(client):
    await login(client, "admin", "admintestpassword")
    await _reg(client, "eve", "secret1")
    eve_id = await _get_id(client, "eve")
    await client.put(f"/api/users/{eve_id}/role", json={"role": "banned"})

    # 正确凭据 → 403；错误凭据 → 401（凭据校验在前）
    resp = await client.post("/api/auth/login", json={"username": "eve", "password": "secret1"})
    assert resp.status_code == 403
    assert resp.json()["msg"] == "user is banned"
    resp = await client.post("/api/auth/login", json={"username": "eve", "password": "wrong1"})
    assert resp.status_code == 401


async def test_session_expiry(client):
    await _reg(client, "alice", "secret1")
    await login(client, "admin", "admintestpassword")
    alice_id = await _get_id(client, "alice")
    # 直接写入一条已过期的会话，验证服务端 TTL 检查
    async with SessionLocal() as db:
        db.add(Session(
            token="expired-token", user_id=int(alice_id),
            expires_at=datetime.now() - timedelta(seconds=1),
        ))
        await db.commit()
    client.cookies.set(SESSION_COOKIE, "expired-token")
    assert (await client.get("/api/problems/")).status_code == 401


async def test_logout_revokes_session(client):
    await _reg(client, "alice", "secret1")
    await login(client, "alice", "secret1")
    resp = await client.post("/api/auth/logout")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"code": 200, "msg": "logout success", "data": None}
    # 会话从数据库删除（服务端吊销），Cookie 被清除
    async with SessionLocal() as db:
        count = await db.scalar(select(Session))
        assert count is None
    assert client.cookies.get(SESSION_COOKIE) is None
    assert (await client.get("/api/problems/")).status_code == 401


async def test_multiple_sessions_independent(client):
    await _reg(client, "alice", "secret1")
    async with _new_client() as c2:
        await login(client, "alice", "secret1")
        await login(c2, "alice", "secret1")
        assert (await c2.get("/api/problems/")).status_code == 200
        # 一方登出不影响另一方
        await client.post("/api/auth/logout")
        assert (await client.get("/api/problems/")).status_code == 401
        assert (await c2.get("/api/problems/")).status_code == 200


# ---- 用户查询 ----


async def test_get_user_fields(client):
    await _reg(client, "alice", "secret1")
    await login(client, "admin", "admintestpassword")
    alice_id = await _get_id(client, "alice")
    await login(client, "alice", "secret1")
    resp = await client.get(f"/api/users/{alice_id}")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert set(data) == USER_KEYS                 # 不返回密码哈希
    assert data["user_id"] == str(data["user_id"])


async def test_get_user_permissions(client):
    await login(client, "admin", "admintestpassword")
    await _reg(client, "alice", "secret1")
    admin_id = await _get_id(client, "admin")
    await login(client, "alice", "secret1")

    # 查他人 → 403（权限优先于存在性）；不存在的用户同样 403
    assert (await client.get(f"/api/users/{admin_id}")).status_code == 403
    assert (await client.get("/api/users/99999")).status_code == 403

    # 管理员查不存在 → 404
    await login(client, "admin", "admintestpassword")
    assert (await client.get("/api/users/99999")).status_code == 404


async def test_get_user_unauth(client):
    async with _new_client() as c:
        assert (await c.get("/api/users/1")).status_code == 401
    # 非法 user_id（非数字）→ 400
    await _reg(client, "alice", "secret1")
    await login(client, "alice", "secret1")
    assert (await client.get("/api/users/abc")).status_code == 400


async def test_user_stats_semantics(client):
    # submit_count 按提交次数计；resolve_count 按通过（success）的题目数计（api.md）
    await _reg(client, "alice", "secret1")
    await login(client, "admin", "admintestpassword")
    alice_id = int(await _get_id(client, "alice"))
    async with SessionLocal() as db:
        db.add_all([
            Submission(user_id=alice_id, problem_id="p1", language="python", code="print(1)",
                       status="success"),
            Submission(user_id=alice_id, problem_id="p1", language="python", code="print(1)",
                       status="error"),
            Submission(user_id=alice_id, problem_id="p2", language="python", code="print(1)",
                       status="success"),
            Submission(user_id=alice_id, problem_id="p3", language="python", code="print(1)",
                       status="pending"),
        ])
        await db.commit()
    await login(client, "alice", "secret1")
    data = (await client.get(f"/api/users/{alice_id}")).json()["data"]
    assert data["submit_count"] == 4
    assert data["resolve_count"] == 2


# ---- 权限变更 ----


async def test_change_role_response_and_log(client):
    await login(client, "admin", "admintestpassword")
    await _reg(client, "carol", "secret1")
    carol_id = await _get_id(client, "carol")

    resp = await client.put(f"/api/users/{carol_id}/role", json={"role": "banned"})
    assert resp.status_code == 200
    assert resp.json() == {
        "code": 200, "msg": "role updated",
        "data": {"user_id": carol_id, "role": "banned"},
    }

    # 操作日志：谁（admin）在何时修改了谁（carol）的权限
    async with SessionLocal() as db:
        logs = (await db.scalars(select(RoleChangeLog))).all()
    assert len(logs) == 1
    log = logs[0]
    assert log.operator_id == 1
    assert log.target_id == int(carol_id)
    assert (log.old_role, log.new_role) == ("user", "banned")
    assert log.time is not None


async def test_change_role_errors(client):
    await login(client, "admin", "admintestpassword")
    await _reg(client, "carol", "secret1")
    carol_id = await _get_id(client, "carol")

    assert (await client.put(f"/api/users/{carol_id}/role", json={"role": "super"})).status_code == 400
    assert (await client.put(f"/api/users/{carol_id}/role", json={})).status_code == 400
    assert (await client.put("/api/users/99999/role", json={"role": "user"})).status_code == 404

    # 非管理员 403（在 404 之前）；未登录 401
    await login(client, "carol", "secret1")
    assert (await client.put(f"/api/users/{carol_id}/role", json={"role": "admin"})).status_code == 403
    assert (await client.put("/api/users/99999/role", json={"role": "user"})).status_code == 403
    async with _new_client() as c:
        assert (await c.put(f"/api/users/{carol_id}/role", json={"role": "admin"})).status_code == 401


async def test_promote_to_admin_gains_privileges(client):
    await login(client, "admin", "admintestpassword")
    await _reg(client, "carol", "secret1")
    carol_id = await _get_id(client, "carol")
    await client.put(f"/api/users/{carol_id}/role", json={"role": "admin"})

    await login(client, "carol", "secret1")
    assert (await client.get("/api/users/")).status_code == 200


async def test_banned_existing_session_blocked(client):
    # 封禁即时生效：已登录的旧会话全部失效
    await _reg(client, "eve", "secret1")
    await login(client, "admin", "admintestpassword")
    eve_id = await _get_id(client, "eve")
    async with _new_client() as c2:
        await login(c2, "eve", "secret1")
        assert (await c2.get("/api/problems/")).status_code == 200

        await login(client, "admin", "admintestpassword")
        await client.put(f"/api/users/{eve_id}/role", json={"role": "banned"})

        assert (await c2.get("/api/problems/")).status_code == 403
        assert (await c2.get(f"/api/users/{eve_id}")).status_code == 403
        # 登出不校验角色：封禁用户仍可登出（会话被删除）
        assert (await c2.post("/api/auth/logout")).status_code == 200
        assert (await c2.get("/api/problems/")).status_code == 401


# ---- 用户列表（管理员） ----


async def _setup_users(client, names=("alice", "bob", "carol")):
    for name in names:
        assert (await _reg(client, name, "secret1")).status_code == 200


async def test_list_users_shape_and_pagination(client):
    await login(client, "admin", "admintestpassword")
    await _setup_users(client)
    total = 4  # admin + alice + bob + carol

    # 无分页参数 → 全部
    data = (await client.get("/api/users/")).json()["data"]
    assert data["total"] == total
    assert len(data["users"]) == total
    assert all(set(u) == USER_KEYS for u in data["users"])

    # 仅 page_size → 第一页（分页语义与提交记录相同）
    data = (await client.get("/api/users/", params={"page_size": 2})).json()["data"]
    assert data["total"] == total
    assert len(data["users"]) == 2

    # page + page_size → 对应页；两页并集为全部用户
    page1 = (await client.get("/api/users/", params={"page": 1, "page_size": 2})).json()["data"]
    page2 = (await client.get("/api/users/", params={"page": 2, "page_size": 2})).json()["data"]
    assert len(page1["users"]) == 2
    assert len(page2["users"]) == 2
    all_names = {u["username"] for u in page1["users"] + page2["users"]}
    assert all_names == {"admin", "alice", "bob", "carol"}


async def test_list_users_invalid_pagination(client):
    await login(client, "admin", "admintestpassword")
    # 仅 page → 400；非法值（0 / 负数 / 非数字）→ 400
    for params in (
        {"page": 1},
        {"page": 0, "page_size": 2},
        {"page": -1, "page_size": 2},
        {"page_size": 0},
        {"page": "abc", "page_size": 2},
    ):
        assert (await client.get("/api/users/", params=params)).status_code == 400


async def test_list_users_permissions(client):
    async with _new_client() as c:                       # 未登录 → 401
        assert (await c.get("/api/users/")).status_code == 401
    await _reg(client, "alice", "secret1")
    await login(client, "alice", "secret1")              # 普通用户 → 403
    assert (await client.get("/api/users/")).status_code == 403


# ---- 创建管理员 ----


async def test_create_admin_response(client):
    await login(client, "admin", "admintestpassword")
    resp = await client.post("/api/users/admin", json={"username": "root2", "password": "rootpw2"})
    assert resp.status_code == 200
    assert resp.json()["msg"] == "success"
    data = resp.json()["data"]
    assert data["user_id"] == str(data["user_id"])
    assert data["username"] == "root2"
    assert data["role"] == "admin"

    # 新管理员可直接登录并拥有管理员权限；操作日志 old_role 记为 "-"
    async with _new_client() as c:
        await login(c, "root2", "rootpw2")
        assert (await c.get("/api/users/")).status_code == 200
    async with SessionLocal() as db:
        log = await db.scalar(select(RoleChangeLog))
    assert (log.old_role, log.new_role) == ("-", "admin")
    assert log.operator_id == 1


async def test_create_admin_errors(client):
    # 未登录 401 / 普通用户 403
    async with _new_client() as c:
        assert (await c.post("/api/users/admin", json={"username": "x1", "password": "secret1"})).status_code == 401
    await _reg(client, "alice", "secret1")
    await login(client, "alice", "secret1")
    assert (await client.post("/api/users/admin", json={"username": "x1", "password": "secret1"})).status_code == 403

    # 管理员创建：同样的用户名/密码校验
    await login(client, "admin", "admintestpassword")
    assert (await client.post("/api/users/admin", json={"username": "ab", "password": "secret1"})).status_code == 400
    assert (await client.post("/api/users/admin", json={"username": "x1", "password": "12345"})).status_code == 400


# ---- reset ----


async def test_reset_clears_everything(client):
    await login(client, "admin", "admintestpassword")
    await _setup_users(client, ("alice", "bob"))

    async with SessionLocal() as db:
        db.add(Submission(user_id=2, problem_id="p1", language="python", code="x", status="success"))
        await db.commit()

    async with _new_client() as c2:                      # 其他用户的旧会话
        await login(c2, "alice", "secret1")

        resp = await client.post("/api/reset/")
        assert resp.status_code == 200
        assert resp.json()["msg"] == "system reset successfully"

        # 提交记录清空；旧会话失效（含管理员自己）；仅剩初始管理员
        async with SessionLocal() as db:
            assert (await db.scalar(select(Submission))) is None
        assert (await c2.get("/api/problems/")).status_code == 401
        assert (await client.get("/api/problems/")).status_code == 401
        await login(client, "admin", "admintestpassword")
        data = (await client.get("/api/users/")).json()["data"]
        assert data["total"] == 1
        assert data["users"][0]["username"] == "admin"
        assert data["users"][0]["role"] == "admin"

    # 初始管理员可用原密码重新登录
    async with _new_client() as c3:
        await login(c3, "admin", "admintestpassword")


async def test_reset_permissions(client):
    async with _new_client() as c:                       # 未登录 → 401
        assert (await c.post("/api/reset/")).status_code == 401
    await _reg(client, "alice", "secret1")
    await login(client, "alice", "secret1")              # 普通用户 → 403
    assert (await client.post("/api/reset/")).status_code == 403
