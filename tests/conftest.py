"""pytest 公共设施：使用独立临时数据目录，不污染开发数据。

注意：OJ_DATA_DIR 必须在导入 app 之前设置（config 在导入时读取）。
"""
import asyncio
import os
import shutil
import tempfile

# 必须在导入 app 之前设置（config 在导入时读取环境变量）
os.environ["OJ_DATA_DIR"] = tempfile.mkdtemp(prefix="oj-test-")
# 测试期间放宽提交限流，避免用例间相互干扰（限流单独测试时用 monkeypatch 收紧）
os.environ["OJ_SUBMIT_RATE_LIMIT"] = "1000"

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app import config
from app.database import Base, engine
from app.main import app
from app.services.user_service import ensure_admin


@pytest_asyncio.fixture
async def client():
    """每个测试用例使用全新的数据库、题目目录与初始管理员。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    shutil.rmtree(config.PROBLEMS_DIR, ignore_errors=True)
    config.PROBLEMS_DIR.mkdir(parents=True, exist_ok=True)
    # AI 模块状态隔离：清空模型配置与密钥，避免用例间相互影响
    (config.DATA_DIR / "ai_config.json").unlink(missing_ok=True)
    (config.DATA_DIR / "ai_secret.key").unlink(missing_ok=True)
    await ensure_admin()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def login(client: AsyncClient, username: str, password: str) -> None:
    """登录并把会话 Cookie 存入 client（httpx 自动复用）。"""
    resp = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text


async def wait_status(client: AsyncClient, submission_id: str, timeout: float = 30) -> dict:
    """轮询提交详情直到评测结束（status != pending）。"""
    for _ in range(int(timeout * 10)):
        resp = await client.get(f"/api/submissions/{submission_id}")
        data = resp.json()["data"]
        if data["status"] != "pending":
            return data
        await asyncio.sleep(0.1)
    raise AssertionError(f"submission {submission_id} still pending after {timeout}s")
