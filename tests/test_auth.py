"""Step 4 认证与用户管理接口测试。"""
from conftest import login


async def test_register_login_logout(client):
    # 注册
    resp = await client.post("/api/users/", json={"username": "bob", "password": "secret1"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["username"] == "bob"
    assert data["role"] == "user"
    assert data["submit_count"] == 0
    assert data["resolve_count"] == 0

    # 重复注册 → 400
    resp = await client.post("/api/users/", json={"username": "bob", "password": "secret1"})
    assert resp.status_code == 400

    # 错误密码 → 401
    resp = await client.post("/api/auth/login", json={"username": "bob", "password": "wrong"})
    assert resp.status_code == 401

    # 未登录 logout → 401
    resp = await client.post("/api/auth/logout")
    assert resp.status_code == 401

    # 登录 / 登出
    await login(client, "bob", "secret1")
    resp = await client.post("/api/auth/logout")
    assert resp.status_code == 200
    # 登出后访问需登录接口 → 401
    resp = await client.get("/api/problems/")
    assert resp.status_code == 401


async def test_admin_role_management(client):
    await login(client, "admin", "admintestpassword")
    await client.post("/api/users/", json={"username": "carol", "password": "secret2"})

    # 用户列表（管理员）
    resp = await client.get("/api/users/")
    data = resp.json()["data"]
    assert data["total"] == 2  # admin + carol

    carol = next(u for u in data["users"] if u["username"] == "carol")

    # 非管理员查他人 → 403
    await login(client, "carol", "secret2")
    resp = await client.get(f"/api/users/{carol['user_id']}")
    assert resp.status_code == 200  # 查自己允许
    resp = await client.get("/api/users/")
    assert resp.status_code == 403

    # 封禁 carol → 其登录被拒 403
    await login(client, "admin", "admintestpassword")
    resp = await client.put(f"/api/users/{carol['user_id']}/role", json={"role": "banned"})
    assert resp.status_code == 200
    resp = await client.post("/api/auth/login", json={"username": "carol", "password": "secret2"})
    assert resp.status_code == 403

    # 非法角色 → 400
    resp = await client.put(f"/api/users/{carol['user_id']}/role", json={"role": "super"})
    assert resp.status_code == 400

    # 解封
    resp = await client.put(f"/api/users/{carol['user_id']}/role", json={"role": "user"})
    assert resp.status_code == 200


async def test_admin_create_and_reset(client):
    await login(client, "admin", "admintestpassword")

    # 创建管理员
    resp = await client.post("/api/users/admin", json={"username": "root2", "password": "rootpw2"})
    assert resp.status_code == 200
    assert resp.json()["data"]["role"] == "admin"

    # 重复创建 → 400
    resp = await client.post("/api/users/admin", json={"username": "root2", "password": "rootpw2"})
    assert resp.status_code == 400

    # 新管理员可登录
    await login(client, "root2", "rootpw2")

    # 普通用户调用 reset → 403；管理员 → 200 且数据被清空
    await client.post("/api/users/", json={"username": "dave", "password": "secret3"})
    await login(client, "dave", "secret3")
    resp = await client.post("/api/reset/")
    assert resp.status_code == 403

    await login(client, "admin", "admintestpassword")
    resp = await client.post("/api/reset/")
    assert resp.status_code == 200

    # reset 后：会话被清空，需重新登录；dave 消失，admin 仍在
    await login(client, "admin", "admintestpassword")
    resp = await client.get("/api/users/")
    data = resp.json()["data"]
    assert [u["username"] for u in data["users"]] == ["admin"]
