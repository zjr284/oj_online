"""Step 5 评测日志接口测试：测试点明细可见性与访问审计。"""
from conftest import login, wait_status

PROBLEM = {
    "id": "sum_2",
    "title": "两数之和",
    "description": "输入两个整数，输出它们的和。",
    "input_description": "一行两个整数。",
    "output_description": "一个整数。",
    "samples": [{"input": "1 2", "output": "3"}],
    "constraints": "|a|, |b| ≤ 10^9",
    "testcases": [
        {"id": "1", "input": "1 2", "output": "3"},
        {"id": "2", "input": "10 20", "output": "30"},
        {"id": "3", "input": "-5 5", "output": "0"},
        {"id": "4", "input": "0 0", "output": "0"},
    ],
    "time_limit": 1,
    "memory_limit": 64,
}

AC_CODE = "a, b = map(int, input().split())\nprint(a + b)\n"


async def _setup(client) -> str:
    """准备题目/语言/用户，让 bob 提交一次 AC，返回 submission_id。"""
    await login(client, "admin", "admintestpassword")
    assert (await client.post("/api/problems/", json=PROBLEM)).status_code == 200
    assert (await client.post("/api/languages/", json={"name": "python", "file_ext": "py", "run_cmd": "python3 {src}"})).status_code == 200
    await client.post("/api/users/", json={"username": "bob", "password": "pw123456"})
    await client.post("/api/users/", json={"username": "carol", "password": "pw123456"})

    await login(client, "bob", "pw123456")
    resp = await client.post("/api/submissions/", json={"problem_id": "sum_2", "language": "python", "code": AC_CODE})
    sid = resp.json()["data"]["submission_id"]
    await wait_status(client, sid)
    return sid


async def test_log_visibility_and_audit(client):
    sid = await _setup(client)

    # 本人查看：题目未公开 → 无 details 字段，但有 score/counts
    resp = await client.get(f"/api/submissions/{sid}/log")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "details" not in data
    assert data["score"] == 40
    assert data["counts"] == 40   # api.md：本题总分数

    # 非本人查看未公开 → 403
    await login(client, "carol", "pw123456")
    resp = await client.get(f"/api/submissions/{sid}/log")
    assert resp.status_code == 403

    # 管理员查看 → 有 details（4 个测试点）
    await login(client, "admin", "admintestpassword")
    resp = await client.get(f"/api/submissions/{sid}/log")
    data = resp.json()["data"]
    assert len(data["details"]) == 4
    assert set(data["details"][0].keys()) == {"id", "result", "time", "memory"}
    assert all(d["result"] == "AC" for d in data["details"])

    # 公开后：任意登录用户可见 details
    resp = await client.put("/api/problems/sum_2/log_visibility", json={"public_cases": True})
    assert resp.status_code == 200
    await login(client, "carol", "pw123456")
    resp = await client.get(f"/api/submissions/{sid}/log")
    data = resp.json()["data"]
    assert len(data["details"]) == 4

    # 不存在的提交 → 404
    resp = await client.get("/api/submissions/99999/log")
    assert resp.status_code == 404

    # 访问审计：仅管理员可查；记录含允许(200)与拒绝(403)
    resp = await client.get("/api/logs/access/")
    assert resp.status_code == 403   # carol 非管理员
    await login(client, "admin", "admintestpassword")
    resp = await client.get("/api/logs/access/", params={"problem_id": "sum_2"})
    logs = resp.json()["data"]
    assert logs, "应有访问审计记录"
    assert all(l["action"] == "view_logs" for l in logs)
    statuses = [l["status"] for l in logs]
    assert "200" in statuses and "403" in statuses   # api.md 示例：status 为字符串
    assert all(l["problem_id"] == "sum_2" for l in logs)

    # 按用户筛选（carol 的 user_id = 3：一次 403 + 一次 200）
    resp = await client.get("/api/logs/access/", params={"user_id": 3})
    logs = resp.json()["data"]
    assert logs and all(l["user_id"] == "3" for l in logs)
