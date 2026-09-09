"""Step 1 题目管理接口测试：CRUD、校验、权限与鲁棒性边界。"""
import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient

from app import config
from app.database import SessionLocal
from app.main import app
from app.models import AccessLog, AiTask, Submission
from app.services.problem_migration import migrate_problem_references
from app.services.problem_store import ProblemStore
from conftest import login

PROBLEM = {
    "id": "1002",
    "title": "两数之和",
    "description": "输入两个整数，输出它们的和。",
    "input_description": "一行两个整数。",
    "output_description": "一个整数。",
    "samples": [{"input": "1 2", "output": "3"}],
    "constraints": "|a|, |b| ≤ 10^9",
    "testcases": [{"input": "1 2", "output": "3"}],
}

REQUIRED_KEYS = ["id", "title", "description", "input_description",
                 "output_description", "samples", "constraints", "testcases"]
OPTIONAL_DEFAULTS = {"hint": "", "source": "", "tags": [], "time_limit": 3,
                     "memory_limit": 128, "author": "", "difficulty": ""}
EXTRA_FIELDS = {
    "hint": "有负数哦！",
    "source": "洛谷",
    "tags": ["基础题", "模拟"],
    "time_limit": 1.5,
    "memory_limit": 256,
    "author": "Luogu",
    "difficulty": "入门",
}


async def test_crud_and_permissions(client):
    # 未登录 → 401
    resp = await client.get("/api/problems/")
    assert resp.status_code == 401

    await login(client, "admin", "admintestpassword")

    # 创建（api.md：msg = "add success"）
    resp = await client.post("/api/problems/", json=PROBLEM)
    assert resp.status_code == 200
    assert resp.json()["msg"] == "add success"
    assert resp.json()["data"] == {"id": "1002"}

    # 重复创建 → 409
    resp = await client.post("/api/problems/", json=PROBLEM)
    assert resp.status_code == 409

    # 列表与详情（测试使用独立临时数据目录，只含刚创建的题目）
    resp = await client.get("/api/problems/")
    titles = {p["id"]: p["title"] for p in resp.json()["data"]}
    assert titles["1002"] == "两数之和"

    resp = await client.get("/api/problems/1002")
    data = resp.json()["data"]
    assert data["title"] == "两数之和"
    # 可选字段缺省时返回默认值（api.md：str → ""，list → []）
    assert data["hint"] == ""
    assert data["tags"] == []
    assert data["time_limit"] == 3
    assert data["memory_limit"] == 128

    # 更新
    updated = {**PROBLEM, "title": "两数之和（改）", "hint": "有负数哦！"}
    resp = await client.put("/api/problems/1002", json=updated)
    assert resp.status_code == 200
    resp = await client.get("/api/problems/1002")
    assert resp.json()["data"]["title"] == "两数之和（改）"

    # PUT 的 body.id 与路径不一致 → 400
    resp = await client.put("/api/problems/1002", json={**PROBLEM, "id": "2002"})
    assert resp.status_code == 400

    # 普通用户可创建但不能删除 → 403
    await client.post("/api/users/", json={"username": "alice", "password": "pw123456"})
    await login(client, "alice", "pw123456")
    resp = await client.delete("/api/problems/1002")
    assert resp.status_code == 403

    # 管理员删除 → 200，再查 → 404
    await login(client, "admin", "admintestpassword")
    resp = await client.delete("/api/problems/1002")
    assert resp.status_code == 200
    resp = await client.get("/api/problems/1002")
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

    # 路径穿越 id → 400（安全：题目文件不得写出 data/problems/ 之外）
    for evil_id in ("../../evil", "a/b", "..", "x.."):
        resp = await client.post("/api/problems/", json={**PROBLEM, "id": evil_id})
        assert resp.status_code == 400, f"id={evil_id} 应被拒绝"

    # 必填字段为空串 / samples 为空 → 400
    resp = await client.post("/api/problems/", json={**PROBLEM, "title": ""})
    assert resp.status_code == 400
    resp = await client.post("/api/problems/", json={**PROBLEM, "samples": []})
    assert resp.status_code == 400

    # 时间/内存限制非正 → 400
    resp = await client.post("/api/problems/", json={**PROBLEM, "time_limit": 0})
    assert resp.status_code == 400
    resp = await client.post("/api/problems/", json={**PROBLEM, "memory_limit": -1})
    assert resp.status_code == 400


async def test_log_visibility(client):
    """Step 5 的 log_visibility 接口（放在题目管理里实现）。"""
    await login(client, "admin", "admintestpassword")
    await client.post("/api/problems/", json=PROBLEM)

    # 非管理员 → 403
    await client.post("/api/users/", json={"username": "bob", "password": "pw123456"})
    await login(client, "bob", "pw123456")
    resp = await client.put("/api/problems/1002/log_visibility", json={"public_cases": True})
    assert resp.status_code == 403

    # 管理员 → 200
    await login(client, "admin", "admintestpassword")
    resp = await client.put("/api/problems/1002/log_visibility", json={"public_cases": True})
    assert resp.status_code == 200
    assert resp.json()["data"] == {"problem_id": "1002", "public_cases": True}


# ---------- 响应格式与字段语义 ----------

async def test_list_and_detail_response_format(client):
    """列表项只含 {id, title}；详情返回全字段，可选字段带类型默认值。"""
    await login(client, "admin", "admintestpassword")
    assert (await client.post("/api/problems/", json=PROBLEM)).status_code == 200

    resp = await client.get("/api/problems/")
    assert resp.json() == {"code": 200, "msg": "success",
                           "data": [{"id": "1002", "title": "两数之和"}]}

    resp = await client.get("/api/problems/1002")
    body = resp.json()
    assert body["code"] == 200 and body["msg"] == "success"
    data = body["data"]
    assert set(REQUIRED_KEYS) <= set(data)
    for key, default in OPTIONAL_DEFAULTS.items():
        assert data[key] == default
    # api.md：samples/testcases 元素为 {input, output}；未提供编号的测试点不带 id 键
    assert data["samples"] == [{"input": "1 2", "output": "3"}]
    assert data["testcases"] == [{"input": "1 2", "output": "3"}]


async def test_optional_fields_roundtrip(client):
    """可选字段完整往返：入库 → 落盘 → 详情响应一致。"""
    await login(client, "admin", "admintestpassword")
    assert (await client.post("/api/problems/", json={**PROBLEM, **EXTRA_FIELDS})).status_code == 200

    data = (await client.get("/api/problems/1002")).json()["data"]
    for key, value in EXTRA_FIELDS.items():
        assert data[key] == value

    # 落盘：每题一个 JSON 文件，UTF-8 保留中文（ensure_ascii=False）
    path = config.PROBLEMS_DIR / "1002.json"
    raw = path.read_text(encoding="utf-8")
    assert "两数之和" in raw
    assert json.loads(raw)["title"] == "两数之和"


async def test_update_overwrites_whole_config(client):
    """PUT 用请求体整体覆盖原配置：未提交的可选字段回落默认值。"""
    await login(client, "admin", "admintestpassword")
    await client.post("/api/problems/", json={**PROBLEM, "hint": "有负数哦！", "tags": ["基础题"]})

    resp = await client.put("/api/problems/1002", json={**PROBLEM, "title": "改"})
    assert resp.status_code == 200
    assert resp.json() == {"code": 200, "msg": "update success", "data": {"id": "1002"}}

    data = (await client.get("/api/problems/1002")).json()["data"]
    assert data["title"] == "改"
    assert data["hint"] == "" and data["tags"] == []   # 被整体覆盖回默认值
    # 磁盘文件同步更新
    assert json.loads((config.PROBLEMS_DIR / "1002.json").read_text(encoding="utf-8"))["title"] == "改"


async def test_delete_removes_file(client):
    await login(client, "admin", "admintestpassword")
    await client.post("/api/problems/", json=PROBLEM)
    path = config.PROBLEMS_DIR / "1002.json"
    assert path.is_file()
    assert (await client.delete("/api/problems/1002")).status_code == 200
    assert not path.exists()


async def test_multiple_samples_and_testcases_roundtrip(client):
    """多个样例/测试点（含编号）完整往返。"""
    await login(client, "admin", "admintestpassword")
    cfg = {
        **PROBLEM,
        "samples": [{"input": "1 2", "output": "3"}, {"input": "10 20", "output": "30"}],
        "testcases": [
            {"id": "1", "input": "1 2", "output": "3"},
            {"id": "2", "input": "10 20", "output": "30"},
            {"id": "3", "input": "-5 5", "output": "0"},
        ],
    }
    assert (await client.post("/api/problems/", json=cfg)).status_code == 200
    data = (await client.get("/api/problems/1002")).json()["data"]
    assert data["samples"] == cfg["samples"]
    assert data["testcases"] == cfg["testcases"]


# ---------- 权限 ----------

def _req(client, method, path, body=None):
    """GET/DELETE 不支持 json 参数：body 为空时省略。"""
    kwargs = {"json": body} if body is not None else {}
    return getattr(client, method)(path, **kwargs)


@pytest.mark.parametrize("method,path,body", [
    ("get", "/api/problems/", None),
    ("post", "/api/problems/", PROBLEM),
    ("get", "/api/problems/1002", None),
    ("put", "/api/problems/1002", PROBLEM),
    ("delete", "/api/problems/1002", None),
])
async def test_unauthorized_all_endpoints(client, method, path, body):
    """未登录访问任何题目接口 → 401 统一格式（api.md 异常序 401 最优先）。"""
    resp = await _req(client, method, path, body)
    assert resp.status_code == 401
    assert resp.json() == {"code": 401, "msg": "not logged in", "data": None}


async def test_normal_user_cannot_edit_or_delete_problems(client):
    """普通用户可新增和查看，但编辑/删除必须由管理员完成。"""
    await login(client, "admin", "admintestpassword")
    await client.post("/api/users/", json={"username": "alice", "password": "pw123456"})
    await login(client, "alice", "pw123456")

    resp = await client.post("/api/problems/", json={**PROBLEM, "id": "3001"})
    assert resp.status_code == 200
    resp = await client.put("/api/problems/3001", json={**PROBLEM, "id": "3001", "title": "改"})
    assert resp.status_code == 403
    assert (await client.get("/api/problems/3001")).json()["data"]["title"] == "两数之和"
    assert (await client.get("/api/problems/")).status_code == 200

    # 编辑/删除不存在或存在的题目都是 403（权限判断先于资源检查）。
    assert (await client.put(
        "/api/problems/9999", json={**PROBLEM, "id": "9999"},
    )).status_code == 403
    resp = await client.delete("/api/problems/9999")
    assert resp.status_code == 403
    resp = await client.delete("/api/problems/3001")
    assert resp.status_code == 403

    # 管理员：删除不存在的 → 404；删除存在的 → 200
    await login(client, "admin", "admintestpassword")
    assert (await client.delete("/api/problems/9999")).status_code == 404
    assert (await client.delete("/api/problems/3001")).status_code == 200


async def test_banned_user_forbidden(client):
    """封禁用户已有会话后访问 → 403（封禁后无法再登录）。"""
    await login(client, "admin", "admintestpassword")
    await client.post("/api/users/", json={"username": "bob", "password": "pw123456"})

    # bob 独立客户端：先建立会话（封禁后登录会被拒，无法建立会话）
    bob = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await login(bob, "bob", "pw123456")
    assert (await bob.get("/api/problems/")).status_code == 200

    users = (await client.get("/api/users/")).json()["data"]["users"]
    bob_id = next(u["user_id"] for u in users if u["username"] == "bob")
    await client.put(f"/api/users/{bob_id}/role", json={"role": "banned"})

    resp = await bob.get("/api/problems/")
    assert resp.status_code == 403
    assert resp.json() == {"code": 403, "msg": "user is banned", "data": None}
    # 封禁后无法再登录
    resp = await client.post("/api/auth/login", json={"username": "bob", "password": "pw123456"})
    assert resp.status_code == 403


# ---------- 404 与错误格式 ----------

@pytest.mark.parametrize("method,path,body", [
    ("get", "/api/problems/9999", None),
    ("put", "/api/problems/9999", {**PROBLEM, "id": "9999"}),
    ("delete", "/api/problems/9999", None),
])
async def test_not_found(client, method, path, body):
    await login(client, "admin", "admintestpassword")
    resp = await _req(client, method, path, body)
    assert resp.status_code == 404
    assert resp.json() == {"code": 404, "msg": "problem not found", "data": None}


# ---------- 参数校验 ----------

@pytest.mark.parametrize("missing", REQUIRED_KEYS)
async def test_missing_required_field(client, missing):
    """8 个必填字段缺一不可 → 400，且失败创建不落盘。"""
    await login(client, "admin", "admintestpassword")
    resp = await client.post("/api/problems/",
                             json={k: v for k, v in PROBLEM.items() if k != missing})
    assert resp.status_code == 400
    assert resp.json()["code"] == 400 and resp.json()["data"] is None
    assert not (config.PROBLEMS_DIR / "1002.json").exists()


@pytest.mark.parametrize("field,bad_value", [
    ("title", ""),
    ("description", ""),
    ("constraints", ""),
    ("samples", []),
    ("testcases", []),
    ("samples", "not-a-list"),
    ("testcases", "not-a-list"),
    ("samples", [{"input": "1 2"}]),          # 样例缺 output
    ("testcases", [{"output": "3"}]),          # 测试点缺 input
    ("tags", "基础题"),                        # 标签应为列表
    ("time_limit", 0),
    ("time_limit", -1),
    ("time_limit", "abc"),
    ("memory_limit", 0),
    ("memory_limit", -1),
    ("memory_limit", 1.5),                     # 内存限制应为整数
])
async def test_invalid_field_values(client, field, bad_value):
    await login(client, "admin", "admintestpassword")
    resp = await client.post("/api/problems/", json={**PROBLEM, field: bad_value})
    assert resp.status_code == 400, f"{field}={bad_value!r} 应返回 400"
    assert not (config.PROBLEMS_DIR / "1002.json").exists()


@pytest.mark.parametrize("bad_id", [
    "", "..", "../x", "a/b", "a b", "_x", "-x", "x.y", "x" * 65, "中文",
])
async def test_invalid_problem_id_rejected(client, bad_id):
    """非法 id → 400，且不得经 body 触达磁盘（路径穿越防护）。"""
    await login(client, "admin", "admintestpassword")
    resp = await client.post("/api/problems/", json={**PROBLEM, "id": bad_id})
    assert resp.status_code == 400
    assert not (config.PROBLEMS_DIR / f"{bad_id}.json").exists()


@pytest.mark.parametrize("good_id", [
    "0", "1", "1002", "9" * 64, "P1001", "sum_2", "max_num", "range-query",
])
async def test_valid_problem_id_accepted(client, good_id):
    await login(client, "admin", "admintestpassword")
    resp = await client.post("/api/problems/", json={**PROBLEM, "id": good_id})
    assert resp.status_code == 200


async def test_non_json_body(client):
    """请求体不是合法 JSON → 400（非 422）。"""
    await login(client, "admin", "admintestpassword")
    resp = await client.post("/api/problems/", content=b"not-json",
                             headers={"content-type": "application/json"})
    assert resp.status_code == 400
    assert resp.json()["data"] is None


async def test_put_without_body_id(client):
    """PUT 请求体缺 id → 400（id 为必填字段）。"""
    await login(client, "admin", "admintestpassword")
    await client.post("/api/problems/", json=PROBLEM)
    resp = await client.put("/api/problems/1002",
                            json={k: v for k, v in PROBLEM.items() if k != "id"})
    assert resp.status_code == 400


# ---------- 路径参数防御 ----------

@pytest.mark.parametrize("method,path", [
    ("get", "/api/problems/%2E%2E"),
    ("put", "/api/problems/%2E%2E"),
    ("delete", "/api/problems/%2E%2E"),
])
async def test_path_param_id_validated(client, method, path):
    """路径参数 id 与 body 侧同规则校验：非法格式 → 400，不触达存储层。"""
    await login(client, "admin", "admintestpassword")
    await client.post("/api/problems/", json=PROBLEM)
    body = PROBLEM if method == "put" else None
    resp = await _req(client, method, path, body)
    assert resp.status_code == 400
    assert resp.json()["data"] is None


@pytest.mark.parametrize("path", [
    "/api/problems/..%2Fx",
    "/api/problems/..%2F..%2Fevil",
    "/api/problems/a%2Fb",
])
async def test_traversal_paths_unreachable(client, path):
    """含斜杠的多段路径不匹配路由 → 404，落不到存储层。"""
    await login(client, "admin", "admintestpassword")
    resp = await client.get(path)
    assert resp.status_code == 404


# ---------- 无尾斜杠兼容 ----------

async def test_no_trailing_slash_alias(client):
    """api.md 文档路径带尾斜杠，无斜杠形式同样可用（不落静态目录 404）。"""
    await login(client, "admin", "admintestpassword")
    resp = await client.get("/api/problems")
    assert resp.status_code == 200 and resp.json()["data"] == []

    resp = await client.post("/api/problems", json=PROBLEM)
    assert resp.status_code == 200
    assert resp.json() == {"code": 200, "msg": "add success", "data": {"id": "1002"}}

    resp = await client.get("/api/problems")
    assert resp.json()["data"] == [{"id": "1002", "title": "两数之和"}]


# ---------- 并发与损坏容错 ----------

async def test_concurrent_create_same_id(client):
    """并发提交同一 id：恰好一个 200、其余 409，且落盘文件完整。"""
    await login(client, "admin", "admintestpassword")
    responses = await asyncio.gather(*[
        client.post("/api/problems/", json={**PROBLEM, "id": "4001"}) for _ in range(5)
    ])
    codes = sorted(r.status_code for r in responses)
    assert codes.count(200) == 1 and codes.count(409) == 4
    raw = json.loads((config.PROBLEMS_DIR / "4001.json").read_text(encoding="utf-8"))
    assert raw["id"] == "4001" and raw["title"] == PROBLEM["title"]


async def test_create_as_new_assigns_unique_ids_without_overwriting(client):
    """连续保存同一 AI 结果时，每一版都分配新题号且旧题保持不变。"""
    await login(client, "admin", "admintestpassword")

    first = await client.post(
        "/api/problems/?assign_new_id=true",
        json={**PROBLEM, "id": "2001", "title": "第一版"},
    )
    second = await client.post(
        "/api/problems/?assign_new_id=true",
        json={**PROBLEM, "id": "2001", "title": "第二版"},
    )
    third = await client.post(
        "/api/problems/?assign_new_id=true",
        json={**PROBLEM, "id": "2001", "title": "第三版"},
    )

    assert [first.status_code, second.status_code, third.status_code] == [200, 200, 200]
    assert [response.json()["data"]["id"] for response in (first, second, third)] == [
        "2001", "2002", "2003",
    ]
    assert (await client.get("/api/problems/2001")).json()["data"]["title"] == "第一版"
    assert (await client.get("/api/problems/2002")).json()["data"]["title"] == "第二版"
    assert (await client.get("/api/problems/2003")).json()["data"]["title"] == "第三版"


async def test_concurrent_create_as_new_never_reuses_an_id(client):
    """并发另存为新题目也必须全部成功并获得不同题号。"""
    await login(client, "admin", "admintestpassword")
    responses = await asyncio.gather(*[
        client.post(
            "/api/problems/?assign_new_id=true",
            json={**PROBLEM, "id": "5001", "title": f"版本 {index}"},
        )
        for index in range(5)
    ])

    assert all(response.status_code == 200 for response in responses)
    ids = [response.json()["data"]["id"] for response in responses]
    assert len(set(ids)) == 5
    for response, problem_id in zip(responses, ids):
        saved = (await client.get(f"/api/problems/{problem_id}")).json()["data"]
        assert saved["id"] == problem_id


async def test_corrupted_file_tolerance(client):
    """目录中存在损坏 JSON：列表跳过它，其余题目不受影响；读取损坏配置 → 500。"""
    await login(client, "admin", "admintestpassword")
    await client.post("/api/problems/", json=PROBLEM)
    (config.PROBLEMS_DIR / "8001.json").write_text("{ not json", encoding="utf-8")
    (config.PROBLEMS_DIR / "8002.json").write_text('{"id": "8002"}', encoding="utf-8")

    resp = await client.get("/api/problems/")
    assert resp.status_code == 200
    assert resp.json()["data"] == [{"id": "1002", "title": "两数之和"}]

    resp = await client.get("/api/problems/8001")
    assert resp.status_code == 500
    assert resp.json() == {"code": 500, "msg": "problem config corrupted: 8001", "data": None}


async def test_documented_string_ids_survive_startup_migration(tmp_path):
    """api.md 示例 P1001/sum_2 是合法字符串题号，启动时不得改写。"""
    legacy = {**PROBLEM, "id": "P1001", "title": "旧预置题"}
    custom = {**PROBLEM, "id": "old_custom", "title": "旧自定义题"}
    (tmp_path / "P1001.json").write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "old_custom.json").write_text(json.dumps(custom, ensure_ascii=False), encoding="utf-8")

    isolated_store = ProblemStore(tmp_path)
    mapping = await isolated_store.migrate_safe_ids()
    assert mapping == {}
    assert (tmp_path / "P1001.json").is_file()
    assert (tmp_path / "old_custom.json").is_file()
    assert {item["id"] for item in await isolated_store.list_problems()} == {
        "P1001", "old_custom",
    }


async def test_only_unsafe_orphaned_problem_ids_are_migrated(client):
    """安全的历史字符串引用保留；只有无法安全用作文件名的旧值才迁移。"""
    async with SessionLocal() as db:
        db.add(Submission(
            user_id=1, problem_id="legacy_orphan", language="python", code="print(1)",
        ))
        db.add(AccessLog(
            user_id=1, problem_id="legacy_orphan", action="view_logs", status=200,
        ))
        db.add(AiTask(
            user_id=1, requirement="旧任务", problem_id="legacy_orphan",
            result={"id": "generated_legacy", "title": "旧结果"},
        ))
        db.add(Submission(
            user_id=1, problem_id="unsafe id", language="python", code="print(2)",
        ))
        await db.commit()

    mapping = await migrate_problem_references({})
    assert "legacy_orphan" not in mapping
    assert "generated_legacy" not in mapping
    assert mapping["unsafe id"].isdigit()
    async with SessionLocal() as db:
        submission = await db.get(Submission, 1)
        unsafe_submission = await db.get(Submission, 2)
        access = await db.get(AccessLog, 1)
        task = await db.get(AiTask, 1)
        assert submission.problem_id == "legacy_orphan"
        assert unsafe_submission.problem_id == mapping["unsafe id"]
        assert access.problem_id == "legacy_orphan"
        assert task.problem_id == "legacy_orphan"
        assert task.result["id"] == "generated_legacy"
