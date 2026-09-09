"""Step 2 语言注册表测试：列表、动态注册、校验、权限与并发鲁棒性。

测试客户端不经过 lifespan，默认语言用 ensure_languages 手动注册。
"""
import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import SessionLocal
from app.main import app
from app.models import Language
from app.services.language_service import ensure_languages
from conftest import login

BASE = {"name": "go", "file_ext": ".go", "run_cmd": "go run {src}"}


async def test_list_format_and_default_order(client):
    """GET 登录接口：data 为 {name: [...]}，顺序与 api.md 示例一致。"""
    await ensure_languages()
    await login(client, "admin", "admintestpassword")
    resp = await client.get("/api/languages/")
    assert resp.status_code == 200
    assert resp.json() == {"code": 200, "msg": "success", "data": {"name": ["python", "cpp"]}}


async def test_list_empty_when_no_languages(client):
    await login(client, "admin", "admintestpassword")
    resp = await client.get("/api/languages/")
    assert resp.status_code == 200
    assert resp.json()["data"] == {"name": []}


async def test_default_cpp_uses_cpp14_and_repairs_stale_builtin_config(client):
    await ensure_languages()
    async with SessionLocal() as db:
        cpp = await db.get(Language, "cpp")
        assert cpp is not None
        assert cpp.file_ext == ".cpp"
        assert "-std=c++14" in cpp.compile_cmd
        assert cpp.run_cmd == "{exe}"
        cpp.compile_cmd = "g++ -std=c++17 {src} -o {exe}"
        cpp.run_cmd = "./old-main"
        await db.commit()

    await ensure_languages()
    async with SessionLocal() as db:
        cpp = await db.get(Language, "cpp")
        assert "-std=c++14" in cpp.compile_cmd
        assert cpp.run_cmd == "{exe}"


async def test_register_success_and_list_append(client):
    """注册成功：msg/data 对齐 api.md；列表按注册顺序追加。"""
    await login(client, "admin", "admintestpassword")
    resp = await client.post("/api/languages/", json=BASE)
    assert resp.status_code == 200
    assert resp.json() == {"code": 200, "msg": "language registered", "data": {"name": "go"}}

    resp = await client.get("/api/languages/")
    assert resp.json()["data"] == {"name": ["go"]}


async def test_register_requires_login(client):
    assert (await client.get("/api/languages/")).status_code == 401
    resp = await client.post("/api/languages/", json=BASE)
    assert resp.status_code == 401
    assert resp.json() == {"code": 401, "msg": "not logged in", "data": None}


async def test_regular_logged_in_user_can_register(client):
    await client.post("/api/users/", json={"username": "alice", "password": "secret1"})
    await login(client, "alice", "secret1")
    resp = await client.post("/api/languages/", json=BASE)
    assert resp.status_code == 200
    assert (await client.get("/api/languages/")).json()["data"]["name"] == ["go"]


async def test_register_banned_user_forbidden(client):
    """封禁用户注册 → 403。"""
    await login(client, "admin", "admintestpassword")
    await client.post("/api/users/", json={"username": "bob", "password": "pw123456"})
    bob = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await login(bob, "bob", "pw123456")

    users = (await client.get("/api/users/")).json()["data"]["users"]
    bob_id = next(u["user_id"] for u in users if u["username"] == "bob")
    await client.put(f"/api/users/{bob_id}/role", json={"role": "banned"})

    resp = await bob.post("/api/languages/", json=BASE)
    assert resp.status_code == 403
    assert resp.json()["data"] is None
    assert (await bob.get("/api/languages/")).status_code == 403


async def test_register_duplicate(client):
    await login(client, "admin", "admintestpassword")
    assert (await client.post("/api/languages/", json=BASE)).status_code == 200
    resp = await client.post("/api/languages/", json=BASE)
    assert resp.status_code == 400
    assert resp.json() == {"code": 400, "msg": "language already exists", "data": None}
    # 重复注册失败后列表仍只有一条
    assert (await client.get("/api/languages/")).json()["data"]["name"] == ["go"]


async def test_register_concurrent_same_name(client):
    """并发注册同一名称：恰好一个 200、其余 400（唯一约束兜底，不出现 500）。"""
    await login(client, "admin", "admintestpassword")
    responses = await asyncio.gather(*[
        client.post("/api/languages/", json=BASE) for _ in range(5)
    ])
    codes = sorted(r.status_code for r in responses)
    assert codes == [200, 400, 400, 400, 400]
    assert all(r.json()["code"] == r.status_code for r in responses)
    assert (await client.get("/api/languages/")).json()["data"]["name"] == ["go"]


@pytest.mark.parametrize("bad", [
    {},                                            # 全缺
    {"name": "go", "file_ext": ".go"},             # 缺 run_cmd
    {"name": "go", "run_cmd": "go run {src}"},     # 缺 file_ext
    {"file_ext": ".go", "run_cmd": "go run {src}"},  # 缺 name
    {"name": "", "file_ext": ".go", "run_cmd": "go run {src}"},
    {"name": "a/b", "file_ext": ".go", "run_cmd": "go run {src}"},
    {"name": "a b", "file_ext": ".go", "run_cmd": "go run {src}"},
    {"name": "_x", "file_ext": ".go", "run_cmd": "go run {src}"},
    {"name": "-x", "file_ext": ".go", "run_cmd": "go run {src}"},
    {"name": "x" * 33, "file_ext": ".go", "run_cmd": "go run {src}"},   # 超长 name
    {"name": "中文", "file_ext": ".go", "run_cmd": "go run {src}"},
    {"name": "go", "file_ext": "", "run_cmd": "go run {src}"},
    {"name": "go", "file_ext": "g/o", "run_cmd": "go run {src}"},
    {"name": "go", "file_ext": "..py", "run_cmd": "go run {src}"},
    {"name": "go", "file_ext": ".py-c", "run_cmd": "go run {src}"},
    {"name": "go", "file_ext": "a" * 11, "run_cmd": "go run {src}"},    # 超长 file_ext
])
async def test_register_invalid_fields(client, bad):
    await login(client, "admin", "admintestpassword")
    resp = await client.post("/api/languages/", json=bad)
    assert resp.status_code == 400, bad
    assert resp.json()["data"] is None
    assert (await client.get("/api/languages/")).json()["data"] == {"name": []}


@pytest.mark.parametrize("bad", [
    {"name": "go", "file_ext": ".go", "run_cmd": "go run main.go"},     # 缺占位符
    {"name": "go", "file_ext": ".go", "run_cmd": ""},                   # 空命令
    {"name": "go", "file_ext": ".go", "run_cmd": "go run {src}",
     "compile_cmd": "gcc"},                                             # compile_cmd 缺占位符
    {"name": "go", "file_ext": ".go", "run_cmd": "go run {src}", "time_limit": 0},
    {"name": "go", "file_ext": ".go", "run_cmd": "go run {src}", "time_limit": -1},
    {"name": "go", "file_ext": ".go", "run_cmd": "go run {src}", "time_limit": "abc"},
    {"name": "go", "file_ext": ".go", "run_cmd": "go run {src}", "memory_limit": 0},
    {"name": "go", "file_ext": ".go", "run_cmd": "go run {src}", "memory_limit": -1},
    {"name": "go", "file_ext": ".go", "run_cmd": "go run {src}", "memory_limit": 1.5},
])
async def test_register_invalid_commands_and_limits(client, bad):
    await login(client, "admin", "admintestpassword")
    resp = await client.post("/api/languages/", json=bad)
    assert resp.status_code == 400, bad
    assert resp.json()["data"] is None


@pytest.mark.parametrize("good", [
    BASE,                                                      # 解释型：无 compile_cmd
    {**BASE, "file_ext": "go"},                                # 扩展名不带点
    {**BASE, "name": "a.b-c_d"},                               # 点/连字符/下划线合法
    {**BASE, "name": "x" * 32},                                # name 长度边界
    {**BASE, "compile_cmd": "gcc {src} -o {exe}"},             # 编译型
    {**BASE, "compile_cmd": ""},                               # 空 compile_cmd 视为解释型
    {**BASE, "time_limit": 1.0, "memory_limit": 128},
    {**BASE, "run_cmd": "{exe}"},                              # {exe} 占位符
])
async def test_register_valid_configs(client, good):
    await login(client, "admin", "admintestpassword")
    resp = await client.post("/api/languages/", json=good)
    assert resp.status_code == 200, good
    assert resp.json()["msg"] == "language registered"
    assert resp.json()["data"]["name"] == good["name"]


async def test_register_non_json_body(client):
    await login(client, "admin", "admintestpassword")
    resp = await client.post("/api/languages/", content=b"not-json",
                             headers={"content-type": "application/json"})
    assert resp.status_code == 400
    assert resp.json()["data"] is None
