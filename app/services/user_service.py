"""用户服务：注册、初始管理员、用户统计等业务逻辑（Step 4）。"""
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.core.errors import ApiError
from app.core.security import hash_password
from app.database import SessionLocal
from app.models import Submission, User


async def ensure_admin() -> None:
    """启动时创建初始管理员 admin / admintestpassword（api.md 要求）。"""
    async with SessionLocal() as db:
        if await db.scalar(select(User).where(User.username == config.ADMIN_USERNAME)) is None:
            db.add(
                User(
                    username=config.ADMIN_USERNAME,
                    password_hash=hash_password(config.ADMIN_PASSWORD),
                    role="admin",
                )
            )
            await db.commit()


def validate_credentials(username: str, password: str) -> None:
    """Step 4：用户名 3–40 字符，密码至少 6 位。"""
    if not (3 <= len(username) <= 40):
        raise ApiError(400, "username length must be between 3 and 40")
    if len(password) < 6:
        raise ApiError(400, "password must be at least 6 characters")


async def create_user(db: AsyncSession, username: str, password: str, role: str = "user") -> User:
    validate_credentials(username, password)
    if await db.scalar(select(User).where(User.username == username)) is not None:
        raise ApiError(400, "username already exists")
    user = User(username=username, password_hash=hash_password(password), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def user_stats(db: AsyncSession, user_id: int) -> tuple[int, int]:
    """返回 (submit_count, resolve_count)。

    submit_count 按提交次数计；resolve_count 按通过（success）的题目数计（api.md）。
    """
    submit_count = (
        await db.scalar(select(func.count()).select_from(Submission).where(Submission.user_id == user_id))
        or 0
    )
    resolve_count = (
        await db.scalar(
            select(func.count(func.distinct(Submission.problem_id)))
            .select_from(Submission)
            .where(Submission.user_id == user_id, Submission.status == "success")
        )
        or 0
    )
    return submit_count, resolve_count


def user_public(user: User) -> dict:
    """注册/登录返回的用户信息（api.md 示例：user_id 为字符串）。"""
    return {"user_id": str(user.id), "username": user.username, "role": user.role}


async def user_detail(db: AsyncSession, user: User) -> dict:
    """用户详情（含统计信息）。"""
    submit_count, resolve_count = await user_stats(db, user.id)
    return {
        "user_id": str(user.id),
        "username": user.username,
        "join_time": user.join_time.strftime("%Y-%m-%d"),
        "role": user.role,
        "submit_count": submit_count,
        "resolve_count": resolve_count,
    }
