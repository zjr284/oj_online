"""认证依赖：接口权限判断全部在后端完成（api.md 安全要求）。

用法：
    async def some_api(user: User = Depends(get_current_user)): ...      # 需登录
    async def admin_api(user: User = Depends(require_admin)): ...        # 仅管理员
"""
from datetime import datetime

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.database import get_db
from app.models import Session, User

SESSION_COOKIE = "oj_session"


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    """从 Cookie 会话解析当前用户；未登录 401，封禁 403。"""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise ApiError(401, "not logged in")

    session = await db.get(Session, token)
    if session is None or session.expires_at < datetime.now():
        raise ApiError(401, "session expired")

    user = await db.get(User, session.user_id)
    if user is None:
        raise ApiError(401, "user not found")
    if user.role == "banned":
        raise ApiError(403, "user is banned")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """管理员权限；普通用户 403。"""
    if user.role != "admin":
        raise ApiError(403, "admin permission required")
    return user
