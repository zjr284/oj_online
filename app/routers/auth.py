"""Step 4 认证接口：登录 / 登出（注册见 users.py）。"""
import asyncio
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.core.deps import SESSION_COOKIE
from app.core.errors import ApiError, ok
from app.core.security import new_token, verify_password
from app.database import get_db
from app.models import Session, User
from app.schemas.user import LoginIn
from app.services.user_service import user_public

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
async def login(
    body: LoginIn,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    user = await db.scalar(select(User).where(User.username == body.username))
    if user is None or not await asyncio.to_thread(verify_password, body.password, user.password_hash):
        raise ApiError(401, "wrong username or password")
    if user.role == "banned":
        raise ApiError(403, "user is banned")

    token = new_token()
    db.add(Session(token=token, user_id=user.id, expires_at=datetime.now() + timedelta(seconds=config.SESSION_TTL_SECONDS)))
    await db.commit()
    response.set_cookie(SESSION_COOKIE, token, httponly=True, max_age=config.SESSION_TTL_SECONDS, path="/")
    return ok(user_public(user), msg="login success")


@router.post("/logout")
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    token = request.cookies.get(SESSION_COOKIE)
    session = await db.get(Session, token) if token else None
    if session is None or session.expires_at < datetime.now():
        raise ApiError(401, "not logged in")
    await db.delete(session)
    await db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return ok(None, msg="logout success")
