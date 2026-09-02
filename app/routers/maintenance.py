"""测试辅助接口（api.md Step 6）：POST /api/reset/ 仅管理员。

清空用户/题目/提交数据、退出登录、重建初始管理员。
"""
import shutil

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.core.deps import SESSION_COOKIE, require_admin
from app.core.errors import ok
from app.database import Base, engine, get_db
from app.models import User
from app.services.language_service import ensure_languages
from app.services.user_service import ensure_admin

router = APIRouter(tags=["maintenance"])


@router.post("/api/reset/")
async def reset(
    response: Response,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    # 1. 清空数据库（含会话，即“退出登录”）
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    # 2. 清空题目目录
    shutil.rmtree(config.PROBLEMS_DIR, ignore_errors=True)
    config.PROBLEMS_DIR.mkdir(parents=True, exist_ok=True)

    # 3. 重建初始管理员与默认语言（恢复系统初始环境）
    await ensure_admin()
    await ensure_languages()

    response.delete_cookie(SESSION_COOKIE, path="/")
    return ok(None, msg="system reset successfully")
