"""Step 3 评测管理接口测试（真实执行 python3 判题）。"""
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
WA_CODE = "print(1)\n"
TLE_CODE = "while True:\n    pass\n"
MLE_CODE = "data = []\nwhile True:\n    data.append(bytearray(1024 * 1024))\n"
CPP_AC_CODE = """#include <iostream>
int main() {
    int a, b;
    std::cin >> a >> b;
    std::cout << a + b << "\\n";
    return 0;
}
"""


async def _setup(client):
    await login(client, "admin", "admintestpassword")
    resp = await client.post("/api/problems/", json=PROBLEM)
    assert resp.status_code == 200
    resp = await client.post("/api/languages/", json={"name": "python", "file_ext": "py", "run_cmd": "python3 {src}"})
    assert resp.status_code == 200


async def _submit(client, code, problem_id="sum_2", language="python"):
    resp = await client.post(
        "/api/submissions/",
        json={"problem_id": problem_id, "language": language, "code": code},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["submission_id"]


async def test_submit_judge_and_list(client):
    await _setup(client)

    # 未登录提交 → 401
    await client.post("/api/auth/logout")
    resp = await client.post("/api/submissions/", json={"problem_id": "sum_2", "language": "python", "code": AC_CODE})
    assert resp.status_code == 401
    await login(client, "admin", "admintestpassword")

    # 题目/语言不存在 → 404
    resp = await client.post("/api/submissions/", json={"problem_id": "nope", "language": "python", "code": AC_CODE})
    assert resp.status_code == 404
    resp = await client.post("/api/submissions/", json={"problem_id": "sum_2", "language": "ruby", "code": AC_CODE})
    assert resp.status_code == 404

    # 缺少必填字段 → 400
    resp = await client.post("/api/submissions/", json={"problem_id": "sum_2", "language": "python"})
    assert resp.status_code == 400

    # AC 提交 → success；一个测试点 10 分
    sid = await _submit(client, AC_CODE)
    data = await wait_status(client, sid)
    assert data["status"] == "success"
    assert data["score"] == 40
    assert data["counts"] == {"AC": 4}
    assert data["compile_info"] is None   # 解释型语言无编译信息

    # WA 提交 → error
    sid2 = await _submit(client, WA_CODE)
    data = await wait_status(client, sid2)
    assert data["status"] == "error"
    assert data["score"] == 0
    assert data["counts"] == {"WA": 4}
    assert data["run_info"]

    # 列表：按题目筛选；error 记录只返回 id 和 status（api.md）
    resp = await client.get("/api/submissions/", params={"problem_id": "sum_2"})
    data = resp.json()["data"]
    assert data["total"] == 2
    err_item = next(s for s in data["submissions"] if s["submission_id"] == str(sid2))
    assert set(err_item.keys()) == {"submission_id", "status"}

    # 状态筛选
    resp = await client.get("/api/submissions/", params={"problem_id": "sum_2", "status": "success"})
    assert resp.json()["data"]["total"] == 1

    # 分页语义：page 非空 page_size 空 → 400；page 空 page_size 非空 → 第一页
    resp = await client.get("/api/submissions/", params={"problem_id": "sum_2", "page": 1})
    assert resp.status_code == 400
    resp = await client.get("/api/submissions/", params={"problem_id": "sum_2", "page_size": 1})
    assert len(resp.json()["data"]["submissions"]) == 1

    # 详情权限：非本人 → 403；不存在 → 404
    await client.post("/api/users/", json={"username": "alice", "password": "pw123456"})
    await login(client, "alice", "pw123456")
    resp = await client.get(f"/api/submissions/{sid}")
    assert resp.status_code == 403
    resp = await client.get("/api/submissions/99999")
    assert resp.status_code == 404


async def test_tle_and_mle(client):
    await _setup(client)

    # TLE：死循环，CPU 限制 1 秒
    sid = await _submit(client, TLE_CODE)
    data = await wait_status(client, sid, timeout=20)
    assert data["status"] == "error"
    assert data["counts"] == {"TLE": 4}

    # MLE：无限分配内存，限制 64MB
    sid = await _submit(client, MLE_CODE)
    data = await wait_status(client, sid, timeout=20)
    assert data["status"] == "error"
    assert data["counts"] == {"MLE": 4}


async def test_ce(client):
    await _setup(client)
    # 注册 cpp（无论 g++ 是否存在，非法源码都会编译失败 → CE）
    resp = await client.post("/api/languages/", json={
        "name": "cpp", "file_ext": "cpp",
        "compile_cmd": "g++ -O2 {src} -o {exe}", "run_cmd": "{exe}",
    })
    assert resp.status_code == 200
    sid = await _submit(client, "this is not valid c++", language="cpp")
    data = await wait_status(client, sid)
    assert data["status"] == "error"
    assert data["counts"] == {"CE": 1}
    assert data["compile_info"]


async def test_cpp_compile_and_ac(client):
    """C++ 编译回归：源码文件必须带 .cpp 扩展名，否则 g++ 按链接器输入处理 → 全部 CE。
    file_ext 故意不带点（与真实数据库一致），验证判题器自动补全扩展名。"""
    await _setup(client)
    resp = await client.post("/api/languages/", json={
        "name": "cpp", "file_ext": "cpp",
        "compile_cmd": "g++ -O2 -std=c++17 {src} -o {exe}", "run_cmd": "{exe}",
    })
    assert resp.status_code == 200
    sid = await _submit(client, CPP_AC_CODE, language="cpp")
    data = await wait_status(client, sid, timeout=20)
    assert data["status"] == "success"
    assert data["score"] == 40
    assert data["counts"] == {"AC": 4}


async def test_permissions_and_rejudge(client):
    await _setup(client)
    await client.post("/api/users/", json={"username": "alice", "password": "pw123456"})

    # admin 提交并评测完成
    sid = await _submit(client, AC_CODE)
    await wait_status(client, sid)

    # alice 不能看 admin 的提交、不能 rejudge
    await login(client, "alice", "pw123456")
    resp = await client.get(f"/api/submissions/{sid}")
    assert resp.status_code == 403
    resp = await client.put(f"/api/submissions/{sid}/rejudge")
    assert resp.status_code == 403

    # alice 提交，列表只能看到自己的；指定别人的 user_id → 403
    sid2 = await _submit(client, AC_CODE)
    await wait_status(client, sid2)
    resp = await client.get("/api/submissions/")
    data = resp.json()["data"]
    assert data["total"] == 1
    assert data["submissions"][0]["submission_id"] == str(sid2)
    resp = await client.get("/api/submissions/", params={"user_id": 1})
    assert resp.status_code == 403

    # admin rejudge：覆盖为 pending → 重新评测 → success
    await login(client, "admin", "admintestpassword")
    resp = await client.put(f"/api/submissions/{sid2}/rejudge")
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "pending"
    data = await wait_status(client, sid2)
    assert data["status"] == "success"
    # 不存在的提交 rejudge → 404
    resp = await client.put("/api/submissions/99999/rejudge")
    assert resp.status_code == 404


async def test_rate_limit(client, monkeypatch):
    """429：1 分钟内超过 3 次提交。"""
    from app.core.rate_limit import RateLimiter
    import app.routers.submissions as sub_router

    monkeypatch.setattr(sub_router, "submit_limiter", RateLimiter(3, 60))

    await _setup(client)
    for _ in range(3):
        sid = await _submit(client, AC_CODE)
        await wait_status(client, sid)

    resp = await client.post("/api/submissions/", json={"problem_id": "sum_2", "language": "python", "code": AC_CODE})
    assert resp.status_code == 429
