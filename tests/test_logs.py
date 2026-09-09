"""Step 5 日志与权限健壮性测试：评测日志查询、可见性配置、访问审计。

api.md 契约：
- GET /api/submissions/{id}/log：本人（未公开）或管理员；details 仅管理员或
  public_cases=True 时可见；200/403 均记审计；未登录/不存在/参数错误不记。
- PUT /api/problems/{id}/log_visibility：仅管理员；public_cases 选填默认 False。
- GET /api/logs/access/：仅管理员；username/user_id/problem_id 筛选；返回用户名。
- DELETE /api/logs/access/{log_id}：仅管理员可删除单条审计日志。
"""
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.database import SessionLocal
from app.main import app
from app.models import AccessLog, Submission
from app.models import TestcaseResult as _TestcaseResult   # 别名避免被 pytest 当作测试类收集
from conftest import login

PROB = {
    "id": "1001", "title": "t", "description": "d", "input_description": "i",
    "output_description": "o", "constraints": "c",
    "samples": [{"input": "1", "output": "1"}],
    "testcases": [{"input": "1", "output": "1"}],
    "time_limit": 1.0, "memory_limit": 128,
}


def _new_client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed(client, problem_id="1001"):
    """管理员建题 + 注册 alice/bob + 直接写入一条已评测提交（含 3 个测点明细）。

    直接写库避免等待评测，log 接口只读数据库。
    """
    await login(client, "admin", "admintestpassword")
    prob = dict(PROB, id=problem_id)
    resp = await client.post("/api/problems/", json=prob)
    assert resp.status_code == 200, resp.text
    for name in ("alice", "bob"):
        assert (await client.post("/api/users/", json={"username": name, "password": "secret1"})).status_code == 200
    async with SessionLocal() as db:
        db.add(Submission(id=100, user_id=2, problem_id=problem_id, language="python", code="x",
                          status="success", score=20, total_score=30, counts={"AC": 2, "WA": 1}))
        db.add_all([_TestcaseResult(submission_id=100, case_id=str(i), result=r, time=0.1, memory=10)
                    for i, r in enumerate(("AC", "AC", "WA"))])
        await db.commit()


async def _audit_count() -> int:
    async with SessionLocal() as db:
        return await db.scalar(select(func.count()).select_from(AccessLog)) or 0


# ---- 评测日志查询 ----


async def test_log_owner_not_public(client):
    """本人 + 未公开：200 返回 score/counts，裁剪 details（内容裁剪）。"""
    await _seed(client)
    await login(client, "alice", "secret1")
    resp = await client.get("/api/submissions/100/log")
    assert resp.status_code == 200
    body = resp.json()
    assert body["msg"] == "success"
    assert set(body["data"]) == {"score", "counts"}
    assert body["data"]["score"] == 20
    assert body["data"]["counts"] == 30


async def test_log_other_not_public_403_and_audited(client):
    """他人 + 未公开：403，且记入审计（status=403）。"""
    await _seed(client)
    before = await _audit_count()
    await login(client, "bob", "secret1")
    resp = await client.get("/api/submissions/100/log")
    assert resp.status_code == 403
    assert (await _audit_count()) == before + 1
    async with SessionLocal() as db:
        row = (await db.scalars(select(AccessLog).order_by(AccessLog.id.desc()))).first()
    assert (row.user_id, row.problem_id, row.action, row.status) == (3, "1001", "view_logs", 403)


async def test_log_admin_sees_details(client):
    await _seed(client)
    resp = await client.get("/api/submissions/100/log")   # 仍为 admin
    assert resp.status_code == 200
    details = resp.json()["data"]["details"]
    assert [d["result"] for d in details] == ["AC", "AC", "WA"]
    assert all(set(d) == {"id", "result", "time", "memory"} for d in details)


async def test_log_public_everyone_sees_details(client):
    """公开后：所有登录用户可见 details；但 Step 2/3 的简单结果仍不可见。"""
    await _seed(client)
    await client.put("/api/problems/1001/log_visibility", json={"public_cases": True})

    await login(client, "bob", "secret1")
    resp = await client.get("/api/submissions/100/log")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert set(data) == {"score", "counts", "details"}
    assert len(data["details"]) == 3

    # Step 5 任务 1：没权限的用户仍对 Step 2 & 3 评测的简单结果不可见
    assert (await client.get("/api/submissions/100")).status_code == 403


async def test_log_visibility_revoke(client):
    """公开再关闭：他人恢复 403，本人恢复无 details。"""
    await _seed(client)
    await client.put("/api/problems/1001/log_visibility", json={"public_cases": True})
    await client.put("/api/problems/1001/log_visibility", json={"public_cases": False})

    await login(client, "bob", "secret1")
    assert (await client.get("/api/submissions/100/log")).status_code == 403
    await login(client, "alice", "secret1")
    data = (await client.get("/api/submissions/100/log")).json()["data"]
    assert "details" not in data


async def test_log_not_found_no_audit(client):
    """评测不存在 404 不记审计；未登录 401 不记审计；参数错误 400 不记审计。"""
    await _seed(client)
    await login(client, "bob", "secret1")
    before = await _audit_count()
    assert (await client.get("/api/submissions/999/log")).status_code == 404
    assert (await client.get("/api/submissions/abc/log")).status_code == 400
    assert (await _audit_count()) == before

    async with _new_client() as anon:
        assert (await anon.get("/api/submissions/100/log")).status_code == 401
    assert (await _audit_count()) == before


async def test_log_200_audited(client):
    await _seed(client)
    before = await _audit_count()
    await login(client, "alice", "secret1")
    assert (await client.get("/api/submissions/100/log")).status_code == 200
    assert (await _audit_count()) == before + 1


# ---- 配置日志可见性 ----


async def test_log_visibility_response_and_persist(client):
    await _seed(client)
    resp = await client.put("/api/problems/1001/log_visibility", json={"public_cases": True})
    assert resp.status_code == 200
    assert resp.json() == {
        "code": 200, "msg": "log visibility updated",
        "data": {"problem_id": "1001", "public_cases": True},
    }
    # 持久化：题目详情反映 public_cases
    detail = (await client.get("/api/problems/1001")).json()["data"]
    assert detail["public_cases"] is True


async def test_log_visibility_default_false(client):
    await _seed(client)
    # 新建题目 public_cases 默认 False
    detail = (await client.get("/api/problems/1001")).json()["data"]
    assert detail["public_cases"] is False
    # 空 body（选填）→ 恢复 False
    await client.put("/api/problems/1001/log_visibility", json={"public_cases": True})
    resp = await client.put("/api/problems/1001/log_visibility", json={})
    assert resp.status_code == 200
    assert resp.json()["data"]["public_cases"] is False


async def test_log_visibility_permissions(client):
    await _seed(client)
    # 未登录 401 / 普通用户 403（仅管理员）
    async with _new_client() as anon:
        assert (await anon.put("/api/problems/1001/log_visibility", json={"public_cases": True})).status_code == 401
    await login(client, "alice", "secret1")
    assert (await client.put("/api/problems/1001/log_visibility", json={"public_cases": True})).status_code == 403


async def test_log_visibility_errors(client):
    await _seed(client)
    # 题目不存在 404；非法 id 400（路径校验与其它题目接口一致）；非 bool 400
    assert (await client.put("/api/problems/9999/log_visibility", json={"public_cases": True})).status_code == 404
    assert (await client.put("/api/problems/%2E%2E/log_visibility", json={"public_cases": True})).status_code == 400
    assert (await client.put("/api/problems/a%20b/log_visibility", json={"public_cases": True})).status_code == 400
    assert (await client.put("/api/problems/1001/log_visibility", json={"public_cases": "maybe"})).status_code == 400


# ---- 日志访问审计 ----


async def _seed_audits(client, n=3):
    """产生 n 条审计记录：交替 200/403 状态。"""
    await _seed(client)
    await login(client, "alice", "secret1")
    for _ in range(n):
        assert (await client.get("/api/submissions/100/log")).status_code == 200
    await login(client, "bob", "secret1")
    assert (await client.get("/api/submissions/100/log")).status_code == 403
    await login(client, "admin", "admintestpassword")


async def test_access_logs_shape(client):
    await _seed_audits(client)
    resp = await client.get("/api/logs/access/")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert isinstance(data, list) and len(data) == 4
    for row in data:
        assert set(row) == {
            "log_id", "username", "user_id", "problem_id", "action", "time", "status",
        }
        assert row["log_id"].isdigit()
        assert row["action"] == "view_logs"
        assert row["user_id"] == str(row["user_id"])
        assert row["username"] in ("alice", "bob")
        assert row["status"] in ("200", "403")
        assert row["problem_id"] == "1001"
        assert row["time"]


async def test_access_logs_filters(client):
    await _seed_audits(client)
    rows = (await client.get("/api/logs/access/", params={"username": "alice"})).json()["data"]
    assert len(rows) == 3 and all(r["username"] == "alice" for r in rows)
    rows = (await client.get("/api/logs/access/", params={"username": "nobody"})).json()["data"]
    assert rows == []

    # user_id 为 str 参数：数字串匹配，任意字符串返回空数组（api.md 标注 str）
    rows = (await client.get("/api/logs/access/", params={"user_id": "2"})).json()["data"]
    assert rows and all(r["user_id"] == "2" for r in rows)
    rows = (await client.get("/api/logs/access/", params={"user_id": "abc"})).json()["data"]
    assert rows == []

    rows = (await client.get("/api/logs/access/", params={"problem_id": "1001"})).json()["data"]
    assert len(rows) == 4
    rows = (await client.get("/api/logs/access/", params={"problem_id": "9999"})).json()["data"]
    assert rows == []

    # 组合筛选
    rows = (await client.get("/api/logs/access/", params={"user_id": "3", "problem_id": "1001"})).json()["data"]
    assert len(rows) == 1 and rows[0]["status"] == "403"


async def test_access_logs_pagination(client):
    await _seed_audits(client)
    # 全空 = 全部；仅 page_size = 第一页；page+page_size = 对应页
    assert len((await client.get("/api/logs/access/")).json()["data"]) == 4
    page1 = (await client.get("/api/logs/access/", params={"page_size": 2})).json()["data"]
    assert len(page1) == 2
    page2 = (await client.get("/api/logs/access/", params={"page": 2, "page_size": 2})).json()["data"]
    assert len(page2) == 2
    # 按 id 降序（最后一条为 bob 的 403），两页合并为全部且无重叠
    assert [r["status"] for r in page1 + page2] == ["403", "200", "200", "200"]

    # Streamlit 显式请求总数，以显示总页数和指定页跳转。
    result = (await client.get("/api/logs/access/", params={
        "username": "alice", "page": 1, "page_size": 2, "include_total": True,
    })).json()["data"]
    assert result["total"] == 3
    assert len(result["logs"]) == 2
    assert all(row["username"] == "alice" for row in result["logs"])

    # 参数错误：缺参、非正数、非数字及会让 SQLite offset 溢出的极端值。
    for params in (
        {"page": 1},
        {"page": 0, "page_size": 2},
        {"page": -1, "page_size": 2},
        {"page_size": 0},
        {"page_size": 101},
        {"page": 10_000_001, "page_size": 2},
        {"page": "abc", "page_size": 2},
    ):
        assert (await client.get("/api/logs/access/", params=params)).status_code == 400


async def test_access_logs_permissions(client):
    await _seed_audits(client)
    # 未登录 401 / 普通用户 403（仅管理员）
    async with _new_client() as anon:
        assert (await anon.get("/api/logs/access/")).status_code == 401
    await login(client, "alice", "secret1")
    assert (await client.get("/api/logs/access/")).status_code == 403


async def test_admin_can_delete_access_log(client):
    await _seed_audits(client)
    rows = (await client.get("/api/logs/access/")).json()["data"]
    target = rows[0]["log_id"]

    resp = await client.delete(f"/api/logs/access/{target}")
    assert resp.status_code == 200
    assert resp.json() == {
        "code": 200, "msg": "access log deleted", "data": {"log_id": target},
    }
    remaining = (await client.get("/api/logs/access/")).json()["data"]
    assert len(remaining) == 3 and all(row["log_id"] != target for row in remaining)
    assert (await client.delete(f"/api/logs/access/{target}")).status_code == 404


async def test_delete_access_log_permissions(client):
    await _seed_audits(client)
    target = (await client.get("/api/logs/access/")).json()["data"][0]["log_id"]
    async with _new_client() as anon:
        assert (await anon.delete(f"/api/logs/access/{target}")).status_code == 401
    await login(client, "alice", "secret1")
    assert (await client.delete(f"/api/logs/access/{target}")).status_code == 403
    assert await _audit_count() == 4
