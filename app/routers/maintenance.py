"""测试辅助接口（api.md Step 6）：POST /api/reset/ 仅管理员。

清空用户/题目/提交数据、退出登录、重建初始管理员。
"""
import asyncio
import shutil

from app.core.routing import AuthenticatedRoute

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.core.deps import SESSION_COOKIE, require_admin
from app.core.errors import ok
from app.database import Base, engine, get_db
from app.models import User
from app.services.language_service import ensure_languages
from app.services.user_service import ensure_admin
from app.services import ai_service, judge_service
from app.routers import submissions

router = APIRouter(route_class=AuthenticatedRoute, tags=["maintenance"])


@router.post("/api/reset/")
async def reset(
    response: Response,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """管理员重置演示数据；先停止后台任务以避免并发写入。"""
    # 先停止后台写入，防止重置后的 ID 被旧任务结果覆盖。
    await judge_service.shutdown()
    await ai_service.shutdown()
    submissions.submit_limiter._hits.clear()
    # 释放认证读取事务，避免 SQLite drop_all 被当前请求自身阻塞。
    await db.rollback()
    # 1. 清空数据库（含会话，即“退出登录”）
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    # 2. 清空题目目录
    await asyncio.to_thread(shutil.rmtree, config.PROBLEMS_DIR, ignore_errors=True)
    await asyncio.to_thread(config.PROBLEMS_DIR.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(ai_service.CONFIG_PATH.unlink, missing_ok=True)
    await asyncio.to_thread(ai_service.KEY_PATH.unlink, missing_ok=True)

    # 3. 重建初始管理员与默认语言（恢复系统初始环境）
    await ensure_admin()
    await ensure_languages()

    response.delete_cookie(SESSION_COOKIE, path="/")
    return ok(None, msg="system reset successfully")
