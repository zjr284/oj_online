"""Step 1 题目管理接口测试：CRUD、校验、权限。"""
from conftest import login

PROBLEM = {
    "id": "sum_2",
    "title": "两数之和",
    "description": "输入两个整数，输出它们的和。",
    "input_description": "一行两个整数。",
    "output_description": "一个整数。",
    "samples": [{"input": "1 2", "output": "3"}],
    "constraints": "|a|, |b| ≤ 10^9",
    "testcases": [{"input": "1 2", "output": "3"}],
}


async def test_crud_and_permissions(client):
    # 未登录 → 401
    resp = await client.get("/api/problems/")
    assert resp.status_code == 401

    await login(client, "admin", "admintestpassword")

    # 创建
    resp = await client.post("/api/problems/", json=PROBLEM)
    assert resp.status_code == 200
    assert resp.json()["data"] == {"id": "sum_2"}

    # 重复创建 → 409
    resp = await client.post("/api/problems/", json=PROBLEM)
    assert resp.status_code == 409

    # 列表与详情（测试使用独立临时数据目录，只含刚创建的题目）
    resp = await client.get("/api/problems/")
    titles = {p["id"]: p["title"] for p in resp.json()["data"]}
    assert titles["sum_2"] == "两数之和"

    resp = await client.get("/api/problems/sum_2")
    data = resp.json()["data"]
    assert data["title"] == "两数之和"
    # 可选字段缺省时返回默认值（api.md：str → ""，list → []）
    assert data["hint"] == ""
    assert data["tags"] == []
    assert data["time_limit"] == 3
    assert data["memory_limit"] == 128

    # 更新
    updated = {**PROBLEM, "title": "两数之和（改）", "hint": "有负数哦！"}
    resp = await client.put("/api/problems/sum_2", json=updated)
    assert resp.status_code == 200
    resp = await client.get("/api/problems/sum_2")
    assert resp.json()["data"]["title"] == "两数之和（改）"

    # PUT 的 body.id 与路径不一致 → 400
    resp = await client.put("/api/problems/sum_2", json={**PROBLEM, "id": "other"})
    assert resp.status_code == 400

    # 普通用户可创建但不能删除 → 403
    await client.post("/api/users/", json={"username": "alice", "password": "pw123456"})
    await login(client, "alice", "pw123456")
    resp = await client.delete("/api/problems/sum_2")
    assert resp.status_code == 403

    # 管理员删除 → 200，再查 → 404
    await login(client, "admin", "admintestpassword")
    resp = await client.delete("/api/problems/sum_2")
    assert resp.status_code == 200
    resp = await client.get("/api/problems/sum_2")
    assert resp.status_code == 404


async def test_validation(client):
    await login(client, "admin", "admintestpassword")

    # 缺少必填字段 → 400
    bad = {k: v for k, v in PROBLEM.items() if k != "testcases"}
    resp = await client.post("/api/problems/", json=bad)
    assert resp.status_code == 400
    assert resp.json()["code"] == 400
    assert resp.json()["data"] is None

    # 测试点字段类型错误 → 400
    resp = await client.post("/api/problems/", json={**PROBLEM, "samples": "not-a-list"})
    assert resp.status_code == 400


async def test_log_visibility(client):
    """Step 5 的 log_visibility 接口（放在题目管理里实现）。"""
    await login(client, "admin", "admintestpassword")
    await client.post("/api/problems/", json=PROBLEM)

    # 非管理员 → 403
    await client.post("/api/users/", json={"username": "bob", "password": "pw123456"})
    await login(client, "bob", "pw123456")
    resp = await client.put("/api/problems/sum_2/log_visibility", json={"public_cases": True})
    assert resp.status_code == 403

    # 管理员 → 200
    await login(client, "admin", "admintestpassword")
    resp = await client.put("/api/problems/sum_2/log_visibility", json={"public_cases": True})
    assert resp.status_code == 200
    assert resp.json()["data"] == {"problem_id": "sum_2", "public_cases": True}
