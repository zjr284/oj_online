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
    assert data["counts"] == 40           # api.md：本题总分数（4 测试点 × 10）
    assert data["verdicts"] == {"AC": 4}  # extra：各结果统计
    assert data["compile_info"] is None   # 解释型语言无编译信息

    # WA 提交 → error
    sid2 = await _submit(client, WA_CODE)
    data = await wait_status(client, sid2)
    assert data["status"] == "error"
    assert data["score"] == 0
    assert data["counts"] == 40
    assert data["verdicts"] == {"WA": 4}
    assert data["run_info"]["result"] == "finished"

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
    assert data["counts"] == 40
    assert data["verdicts"] == {"TLE": 4}

    # MLE：无限分配内存，限制 64MB
    sid = await _submit(client, MLE_CODE)
    data = await wait_status(client, sid, timeout=20)
    assert data["status"] == "error"
    assert data["counts"] == 40
    assert data["verdicts"] == {"MLE": 4}


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
    assert data["counts"] == 40
    assert data["verdicts"] == {"CE": 1}
    assert data["compile_info"]["result"] == "failed"
    assert data["compile_info"]["message"]


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
    assert data["counts"] == 40
    assert data["verdicts"] == {"AC": 4}


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


async def test_language_validation(client):
    """语言注册配置安全（Step 2 评分点）：非法 name/file_ext/限制 → 400。"""
    await login(client, "admin", "admintestpassword")
    base = {"name": "go", "file_ext": ".go", "run_cmd": "go run {src}"}

    # 未登录 → 401
    await client.post("/api/auth/logout")
    resp = await client.post("/api/languages/", json=base)
    assert resp.status_code == 401
    await login(client, "admin", "admintestpassword")

    # 非法 name / file_ext → 400（杜绝路径分隔符、空扩展名等）
    for bad in ({"name": "a/b", "file_ext": ".go", "run_cmd": "go run {src}"},
                {"name": "go", "file_ext": "g/o", "run_cmd": "go run {src}"},
                {"name": "go", "file_ext": "", "run_cmd": "go run {src}"}):
        resp = await client.post("/api/languages/", json=bad)
        assert resp.status_code == 400, bad

    # 非正限制 → 400
    resp = await client.post("/api/languages/", json={**base, "time_limit": -1})
    assert resp.status_code == 400
    resp = await client.post("/api/languages/", json={**base, "memory_limit": 0})
    assert resp.status_code == 400

    # run_cmd 必须含 {src}/{exe} 占位符（api.md 示例强调路径）
    resp = await client.post("/api/languages/", json={"name": "go", "file_ext": ".go", "run_cmd": "go run main.go"})
    assert resp.status_code == 400

    # 合法注册 → 200（msg 对齐 api.md）
    resp = await client.post("/api/languages/", json=base)
    assert resp.status_code == 200
    assert resp.json()["msg"] == "language registered"


# ---------- Step 2 评测引擎鲁棒性 ----------

async def test_output_tolerance(client):
    """Step 2 输出比对：忽略行末空格与最后一行多余换行。"""
    await _setup(client)
    # 每行行尾多余空格 + 末尾多个空行 → AC
    code = ("a, b = map(int, input().split())\n"
            "print(a + b, end='   ')\n"
            "print('   ')\nprint()\n")
    data = await wait_status(client, await _submit(client, code))
    assert data["verdicts"] == {"AC": 4}

    # Windows 风格 \\r\\n 换行 → AC
    code = "a, b = map(int, input().split())\nprint(a + b, end='\\r\\n')\n"
    data = await wait_status(client, await _submit(client, code))
    assert data["verdicts"] == {"AC": 4}


async def test_mixed_verdicts_score(client):
    """部分通过：按测试点计分（每点 10 分），verdicts 汇总各结果。"""
    await _setup(client)
    code = "a, b = map(int, input().split())\nprint(a + b if a == 1 else 999)\n"
    data = await wait_status(client, await _submit(client, code))
    assert data["status"] == "error"
    assert data["verdicts"] == {"AC": 1, "WA": 3}
    assert data["score"] == 10
    assert data["counts"] == 40           # 总分数不因 WA 改变


async def test_wall_clock_tle(client):
    """sleep 类程序不消耗 CPU：墙钟超时兜底 → TLE。"""
    await _setup(client)
    code = "import time\ntime.sleep(5)\nprint(3)\n"
    data = await wait_status(client, await _submit(client, code), timeout=30)
    assert data["status"] == "error"
    assert data["verdicts"] == {"TLE": 4}
    assert data["counts"] == 40


async def test_runtime_error_re(client):
    """非零退出 → RE，run_info 标记评测正常结束。"""
    await _setup(client)
    code = "raise RuntimeError('boom')\n"
    data = await wait_status(client, await _submit(client, code))
    assert data["status"] == "error"
    assert data["verdicts"] == {"RE": 4}
    assert data["score"] == 0
    assert data["run_info"]["result"] == "finished"


async def test_unknown_error_unk(client):
    """run_cmd 指向不存在的可执行文件 → UNK（不抛 500）。"""
    await _setup(client)
    await client.post("/api/languages/", json={
        "name": "ghost", "file_ext": ".py", "run_cmd": "no-such-binary-xyz {src}"})
    data = await wait_status(client, await _submit(client, "print(3)\n", language="ghost"))
    assert data["status"] == "error"
    assert data["verdicts"] == {"UNK": 4}
    assert data["run_info"]["result"] == "finished"


async def test_missing_compiler_ce(client):
    """编译命令指向不存在的编译器 → CE（而非 500/UNK）。"""
    await _setup(client)
    await client.post("/api/languages/", json={
        "name": "phantomc", "file_ext": ".cpp",
        "compile_cmd": "no-such-compiler-xyz {src} -o {exe}", "run_cmd": "{exe}"})
    data = await wait_status(client, await _submit(client, "int main(){}", language="phantomc"))
    assert data["status"] == "error"
    assert data["verdicts"] == {"CE": 1}
    assert data["compile_info"]["result"] == "failed"
    assert data["compile_info"]["message"]


async def test_dynamic_language_registration_affects_judge(client):
    """注册的别名语言立即参与评测（Step 2 动态注册评分点）。"""
    await _setup(client)
    resp = await client.post("/api/languages/", json={
        "name": "py2", "file_ext": ".py", "run_cmd": "python3 {src}"})
    assert resp.status_code == 200
    data = await wait_status(client, await _submit(client, AC_CODE, language="py2"))
    assert data["status"] == "success"
    assert data["verdicts"] == {"AC": 4}
    assert data["score"] == 40


async def test_language_limits_override_problem(client):
    """语言注册的 time_limit 优先于题目配置（Step 2：题目未设置时按语言配置）。"""
    await login(client, "admin", "admintestpassword")
    await client.post("/api/problems/", json={
        **{k: v for k, v in PROBLEM.items() if k not in ("id", "testcases")},
        "id": "slow", "time_limit": 5,
        "testcases": [{"id": "1", "input": "1 2", "output": "3"}],
    })
    await client.post("/api/languages/", json={"name": "python", "file_ext": "py", "run_cmd": "python3 {src}"})
    await client.post("/api/languages/", json={
        "name": "turtle", "file_ext": ".py", "run_cmd": "python3 {src}",
        "time_limit": 1, "memory_limit": 64,
    })

    # 题目限制 5s 下 sleep(3) 本可通过；语言限制 1s → TLE
    code = "import time\ntime.sleep(3)\nprint(3)\n"
    data = await wait_status(client, await _submit(client, code, problem_id="slow", language="turtle"),
                             timeout=30)
    assert data["status"] == "error"
    assert data["verdicts"] == {"TLE": 1}

    # 用默认语言（无语言限制）提交同样代码：走题目 5s 限制 → AC
    data = await wait_status(client, await _submit(client, code, problem_id="slow", language="python"),
                             timeout=30)
    assert data["status"] == "success"
    assert data["verdicts"] == {"AC": 1}


async def test_empty_code_rejected(client):
    """空代码 → 400（code 必填非空）。"""
    await _setup(client)
    resp = await client.post("/api/submissions/",
                             json={"problem_id": "sum_2", "language": "python", "code": ""})
    assert resp.status_code == 400
    assert resp.json()["data"] is None
