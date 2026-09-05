"""Advance：AI 智能命题接口测试（模型调用经 monkeypatch mock，不依赖外部服务）。"""
import asyncio
import json

import httpx

from app.main import app
from conftest import login

PROBLEM = {
    "id": "1002",
    "title": "两数之和",
    "description": "输入两个整数，输出它们的和。",
    "input_description": "一行两个整数。",
    "output_description": "一个整数。",
    "samples": [{"input": "1 2", "output": "3"}],
    "constraints": "|a|, |b| ≤ 10^9",
    "testcases": [
        {"id": "1", "input": "1 2", "output": "3"},
        {"id": "2", "input": "10 20", "output": "30"},
    ],
    "time_limit": 1,
    "memory_limit": 64,
}

CONFIG = {
    "provider_url": "https://api.example.com/v1/chat/completions",
    "model": "test-model",
    "api_key": "sk-secret-key-123456",
    "input_price": 0.1,
    "output_price": 0.2,
    "price_unit": 1000000,
}

GENERATED = {
    "id": "2001",
    "title": "AI 生成的题目",
    "description": "题目描述",
    "input_description": "输入格式",
    "output_description": "输出格式",
    "samples": [{"input": "1", "output": "2"}],
    "constraints": "n ≤ 100",
    "testcases": [
        {"id": "1", "input": "1", "output": "2"},
        {"id": "2", "input": "0", "output": "1"},
    ],
    "hint": "提示",
    "source": "AI",
    "tags": ["AI", "入门"],
    "time_limit": 1.0,
    "memory_limit": 64,
    "author": "AI",
    "difficulty": "简单",
}

FAKE_USAGE = {"prompt_tokens": 100, "completion_tokens": 200}


def _mock_model(monkeypatch, content=None, usage=None, delay=0.0, exc=None):
    """替换 ai_service._request_model（底层 HTTP 调用），保留真实的
    响应解析、题目校验与费用计算管道。content 缺省返回合法题目 JSON。"""
    import app.services.ai_service as svc

    async def fake(cfg, payload):
        if delay:
            await asyncio.sleep(delay)
        if exc is not None:
            raise exc
        return {
            "choices": [{"message": {"content": content if content is not None else json.dumps(GENERATED, ensure_ascii=False)}}],
            "usage": usage if usage is not None else FAKE_USAGE,
        }

    monkeypatch.setattr(svc, "_request_model", fake)


async def _config(client):
    await login(client, "admin", "admintestpassword")
    resp = await client.put("/api/ai/model-config", json=CONFIG)
    assert resp.status_code == 200
    return resp


async def _wait_task(client, tid, timeout=15):
    for _ in range(int(timeout * 10)):
        resp = await client.get(f"/api/ai/problem-tasks/{tid}")
        assert resp.status_code == 200
        d = resp.json()["data"]
        if d["status"] in ("done", "cancelled", "failed"):
            return d
        await asyncio.sleep(0.1)
    raise AssertionError(f"task {tid} not finished in {timeout}s")


async def test_model_config_security(client):
    # 未登录 → 401
    resp = await client.put("/api/ai/model-config", json=CONFIG)
    assert resp.status_code == 401

    resp = await _config(client)
    data = resp.json()["data"]
    assert data["provider_url"] == CONFIG["provider_url"]
    assert data["model"] == CONFIG["model"]
    assert data["api_key_configured"] is True
    assert "api_key" not in data   # 密钥绝不返回（api.md 安全要求）

    # GET 查询同样不含密钥
    resp = await client.get("/api/ai/model-config")
    data = resp.json()["data"]
    assert data["api_key_configured"] is True and "api_key" not in data
    assert data["price_unit"] == 1000000

    # 磁盘上的配置文件不存明文密钥
    from app import config
    text = (config.DATA_DIR / "ai_config.json").read_text(encoding="utf-8")
    assert "sk-secret-key-123456" not in text

    # 非法 provider_url / 负价格 → 400
    bad = dict(CONFIG, provider_url="ftp://x")
    assert (await client.put("/api/ai/model-config", json=bad)).status_code == 400
    bad = dict(CONFIG, input_price=-1)
    assert (await client.put("/api/ai/model-config", json=bad)).status_code == 400


async def test_get_config_unconfigured_and_events_guards(client):
    # 未配置模型：GET 返回 api_key_configured: false（不 404/500）
    await login(client, "admin", "admintestpassword")
    resp = await client.get("/api/ai/model-config")
    assert resp.status_code == 200
    assert resp.json()["data"] == {"api_key_configured": False}

    # events 接口：未登录 401 / 任务不存在 404（均在响应开始前返回）
    anon = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    async with anon:
        assert (await anon.get("/api/ai/problem-tasks/1/events")).status_code == 401
    assert (await client.get("/api/ai/problem-tasks/99999/events")).status_code == 404
    assert (await client.get("/api/ai/problem-tasks/99999")).status_code == 404
    assert (await client.put("/api/ai/problem-tasks/99999/cancel")).status_code == 404


async def test_restart_stale_tasks_marked_failed(client):
    """进程重启后遗留的 pending/running 任务标记为 failed（api.md 失败兜底）。"""
    from app.database import SessionLocal
    from app.models import AiTask
    from app.services import ai_service
    # 直接写库构造“上次进程遗留”的 pending/running 任务（不启动真实后台任务）
    await login(client, "admin", "admintestpassword")
    async with SessionLocal() as db:
        db.add(AiTask(user_id=1, requirement="遗留任务", status="pending"))
        db.add(AiTask(user_id=1, requirement="遗留任务2", status="running"))
        await db.commit()
    await ai_service.fail_stale_tasks()
    for tid in (1, 2):
        d = (await client.get(f"/api/ai/problem-tasks/{tid}")).json()["data"]
        assert d["status"] == "failed"
        assert "restart" in d["result"]["error"]


async def test_no_config_and_missing_problem(client):
    await login(client, "admin", "admintestpassword")
    # 未配置模型 → 400
    resp = await client.post("/api/ai/problem-tasks/", json={"requirement": "出一题"})
    assert resp.status_code == 400
    await _config(client)
    # 参考题目不存在 → 404（api.md）
    resp = await client.post("/api/ai/problem-tasks/", json={"requirement": "出一题", "problem_id": "9999"})
    assert resp.status_code == 404
    # requirement 缺失 → 400
    resp = await client.post("/api/ai/problem-tasks/", json={})
    assert resp.status_code == 400


async def test_task_flow_cost_and_import(client, monkeypatch):
    await _config(client)
    await client.post("/api/problems/", json=PROBLEM)
    _mock_model(monkeypatch)

    resp = await client.post("/api/ai/problem-tasks/", json={"requirement": "出一道入门题"})
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "pending"
    tid = body["task_id"]

    d = await _wait_task(client, tid)
    assert d["status"] == "done"
    assert d["result"]["id"] == "2001"
    # 费用公式：输入Token/单位×输入单价 + 输出Token/单位×输出单价
    u = d["usage"]
    assert u["input_tokens"] == 100 and u["output_tokens"] == 200 and u["total_tokens"] == 300
    assert u["cost"] == 0.00005   # 100/1e6*0.1 + 200/1e6*0.2
    assert u["currency"] == "CNY" and u["estimated"] is False

    # 生成结果不直接写题库（与基础功能解耦，经已有接口导入）
    resp = await client.get("/api/problems/2001")
    assert resp.status_code == 404
    # 用户可经既有 POST /api/problems/ 导入生成结果（R1 衔接）
    resp = await client.post("/api/problems/", json=d["result"])
    assert resp.status_code == 200
    resp = await client.get("/api/problems/2001")
    assert resp.status_code == 200


async def test_task_list_and_permissions(client, monkeypatch):
    await _config(client)
    await client.post("/api/users/", json={"username": "bob", "password": "pw123456"})
    await client.post("/api/users/", json={"username": "carol", "password": "pw123456"})
    _mock_model(monkeypatch)

    # 普通用户不能使用任何 AI 命题接口。
    await login(client, "bob", "pw123456")
    assert (await client.get("/api/ai/model-config")).status_code == 403
    assert (await client.put("/api/ai/model-config", json=CONFIG)).status_code == 403
    assert (await client.post("/api/ai/problem-tasks/", json={"requirement": "出一道题"})).status_code == 403
    assert (await client.get("/api/ai/problem-tasks/")).status_code == 403

    # 管理员可以创建并查看任务；列表不含 result。
    await login(client, "admin", "admintestpassword")
    resp = await client.post("/api/ai/problem-tasks/", json={"requirement": "出一道题"})
    tid = resp.json()["data"]["task_id"]
    await _wait_task(client, tid)
    assert (await client.get(f"/api/ai/problem-tasks/{tid}")).status_code == 200
    resp = await client.get("/api/ai/problem-tasks/")
    assert len(resp.json()["data"]) == 1
    assert "result" not in resp.json()["data"][0]
    # 不存在的任务 → 404
    assert (await client.get("/api/ai/problem-tasks/99999")).status_code == 404


async def test_cancel_really_terminates(client, monkeypatch):
    await _config(client)
    _mock_model(monkeypatch, delay=10)

    resp = await client.post("/api/ai/problem-tasks/", json={"requirement": "出一道题"})
    tid = resp.json()["data"]["task_id"]
    # 等任务进入 running（模型调用被 sleep 阻塞）
    for _ in range(100):
        d = (await client.get(f"/api/ai/problem-tasks/{tid}")).json()["data"]
        if d["status"] == "running":
            break
        await asyncio.sleep(0.05)

    resp = await client.put(f"/api/ai/problem-tasks/{tid}/cancel")
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "cancelled"
    # 已结束再取消 → 409（api.md）
    resp = await client.put(f"/api/ai/problem-tasks/{tid}/cancel")
    assert resp.status_code == 409

    # 真正终止：等待后仍为 cancelled，而不是被后台任务改成 done
    await asyncio.sleep(0.5)
    d = (await client.get(f"/api/ai/problem-tasks/{tid}")).json()["data"]
    assert d["status"] == "cancelled"


async def test_invalid_model_output_and_secret_leak(client, monkeypatch):
    await _config(client)

    async def run(content=None, exc=None):
        _mock_model(monkeypatch, content=content, exc=exc)
        resp = await client.post("/api/ai/problem-tasks/", json={"requirement": "出一道题"})
        tid = resp.json()["data"]["task_id"]
        return await _wait_task(client, tid)

    # 输出不是 JSON → failed
    d = await run(content="这不是 JSON")
    assert d["status"] == "failed"
    assert "not valid JSON" in d["result"]["error"]

    # 输出是 JSON 但缺必填字段 → failed（校验模型返回数据）
    d = await run(content='{"id": "x", "title": "t"}')
    assert d["status"] == "failed"
    assert "validation" in d["result"]["error"]

    # 异常信息含 api_key → 脱敏（api.md：不得在错误信息中泄露密钥）
    d = await run(exc=RuntimeError(f"connect fail with key sk-secret-key-123456"))
    assert d["status"] == "failed"
    assert "sk-secret-key-123456" not in d["result"]["error"]
    assert "***" in d["result"]["error"]


async def test_empty_content_retry_and_hint(client, monkeypatch):
    """模型返回空内容：自动重试一次；仍为空则报错并带排查提示。"""
    import app.services.ai_service as svc
    await _config(client)

    async def run_once(responses):
        calls = {"n": 0}

        async def fake(*_args):
            r = responses[min(calls["n"], len(responses) - 1)]
            calls["n"] += 1
            return r

        monkeypatch.setattr(svc, "_request_model", fake)
        resp = await client.post("/api/ai/problem-tasks/", json={"requirement": "出一道题"})
        return await _wait_task(client, resp.json()["data"]["task_id"])

    empty = {"choices": [{"message": {"content": ""}}], "usage": FAKE_USAGE}
    good = {"choices": [{"message": {"content": json.dumps(GENERATED, ensure_ascii=False)}}], "usage": FAKE_USAGE}
    length = {"choices": [{"message": {"content": ""}, "finish_reason": "length"}], "usage": FAKE_USAGE}

    # 第一次空、第二次正常 → 重试成功
    d = await run_once([empty, good])
    assert d["status"] == "done"

    # 两次都空 → failed，提示检查官方模型 ID
    d = await run_once([empty, empty])
    assert d["status"] == "failed"
    assert "empty content" in d["result"]["error"]
    assert "deepseek-chat" in d["result"]["error"]

    # 输出被 max_tokens 截断 → 专门提示
    d = await run_once([length, length])
    assert d["status"] == "failed"
    assert "max_tokens" in d["result"]["error"]


async def test_auto_pricing(client, monkeypatch):
    """费用来源优先级：提供方返回费用 / 手动配置价格 / 无法确定。"""
    await login(client, "admin", "admintestpassword")

    async def new_task(model, usage=None, manual=None):
        cfg = {
            "provider_url": "https://api.example.com/v1/chat/completions",
            "model": model,
            "api_key": "sk-auto-1",
        }
        if manual is not None:
            cfg.update(manual)
        assert (await client.put("/api/ai/model-config", json=cfg)).status_code == 200
        _mock_model(monkeypatch, usage=usage)
        resp = await client.post("/api/ai/problem-tasks/", json={"requirement": "出一道题"})
        tid = resp.json()["data"]["task_id"]
        return await _wait_task(client, tid)

    # 1. 提供方返回费用 → 最优先
    d = await new_task("some-model", usage={"prompt_tokens": 100, "completion_tokens": 200, "cost": 0.123456})
    u = d["usage"]
    assert u["price_source"] == "provider"
    assert u["cost"] == 0.123456

    # 2. 手动配置价格
    d = await new_task("some-model", manual={"input_price": 1.0, "output_price": 2.0, "price_unit": 1000})
    u = d["usage"]
    assert u["price_source"] == "config"
    assert u["cost"] == 0.5        # 100/1000*1 + 200/1000*2

    # 3. 未填价格且接口未返回费用 → cost None + unknown 标注
    d = await new_task("some-model")
    u = d["usage"]
    assert u["price_source"] == "unknown"
    assert u["cost"] is None


async def test_request_model_404_hint(client, monkeypatch):
    """模型接口返回 404（如 provider_url 只填了域名）时给出明确提示。"""
    import app.services.ai_service as svc

    class FakeResp:
        status_code = 404
        text = ""

    class FakeClient:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, *a, **kw):
            return FakeResp()

    monkeypatch.setattr(svc.httpx, "AsyncClient", FakeClient)
    try:
        await svc._request_model({"provider_url": "https://api.deepseek.com", "api_key": "sk-x"}, {"messages": []})
    except RuntimeError as e:
        assert "HTTP 404" in str(e)
        assert "/chat/completions" in str(e)   # 提示用户补全接口路径
    else:
        raise AssertionError("should raise RuntimeError")


async def test_progress_continuous(client, monkeypatch):
    """advance.md R3：模型调用期间进度持续推送，而非等任务完成才返回结果。"""
    await _config(client)
    _mock_model(monkeypatch, delay=4.6)

    resp = await client.post("/api/ai/problem-tasks/", json={"requirement": "出一道题"})
    tid = resp.json()["data"]["task_id"]
    async with client.stream("GET", f"/api/ai/problem-tasks/{tid}/events") as r:
        text = (await r.aread()).decode()

    # 订阅前的进度由 state 快照表示；验证调用尚未结束时确实发出了两次更新。
    assert "模型推理中（已 2s）" in text
    assert "模型推理中（已 4s）" in text
    assert "event: final" in text


async def test_cancel_sse_notifies(client, monkeypatch):
    """advance.md R3：中断后 SSE 立即推送 cancelled 终态（界面明确展示已中断）。

    注：httpx 的 ASGITransport 打开流会阻塞到响应结束，无法并发 cancel，
    故直接消费 SSE 生成器验证事件链路（浏览器中 EventSource 与 fetch 是独立连接）。
    """
    import app.services.ai_service as svc
    await _config(client)
    _mock_model(monkeypatch, delay=10)

    resp = await client.post("/api/ai/problem-tasks/", json={"requirement": "出一道题"})
    tid = resp.json()["data"]["task_id"]
    # 等任务进入 running（模型调用被 sleep 阻塞）
    for _ in range(100):
        d = (await client.get(f"/api/ai/problem-tasks/{tid}")).json()["data"]
        if d["status"] == "running":
            break
        await asyncio.sleep(0.05)

    state = (await client.get(f"/api/ai/problem-tasks/{tid}")).json()["data"]
    stream = svc.sse_stream(state, tid)
    first = await asyncio.wait_for(anext(stream), timeout=3)   # 首帧：当前状态
    assert "event: state" in first and '"status": "running"' in first

    resp = await client.put(f"/api/ai/problem-tasks/{tid}/cancel")
    assert resp.status_code == 200
    # cancel 后流应立即收到 cancelled 终态并结束（不推送则此读取超时失败）
    frame = await asyncio.wait_for(anext(stream), timeout=3)
    assert "event: final" in frame
    assert '"status": "cancelled"' in frame or '"status":"cancelled"' in frame


async def test_events_sse(client, monkeypatch):
    await _config(client)
    _mock_model(monkeypatch, delay=0.3)

    resp = await client.post("/api/ai/problem-tasks/", json={"requirement": "出一道题"})
    tid = resp.json()["data"]["task_id"]

    async with client.stream("GET", f"/api/ai/problem-tasks/{tid}/events") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        text = (await r.aread()).decode()

    assert "event: state" in text
    assert "event: progress" in text
    assert "event: final" in text
    assert '"status": "done"' in text or '"status":"done"' in text
    # 权限：carol 访问 events → 403
    await client.post("/api/users/", json={"username": "carol", "password": "pw123456"})
    await login(client, "carol", "pw123456")
    resp = await client.get(f"/api/ai/problem-tasks/{tid}/events")
    assert resp.status_code == 403
