"""Step 4 用户管理接口。

分页语义（api.md）：page 与 page_size 全空 = 查全部；
page 空 page_size 非空 = 第一页；page 非空 page_size 空 = 参数错误。
"""
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, require_admin
from app.core.errors import ApiError, ok
from app.database import get_db
from app.models import User
from app.schemas.user import RegisterIn, RoleIn
from app.services.user_service import create_user, user_detail, user_public

router = APIRouter(prefix="/api/users", tags=["users"])

VALID_ROLES = ("admin", "user", "banned")


def _apply_paging(stmt, page: int | None, page_size: int | None):
    """按 api.md 语义处理分页参数；非法组合抛 400。"""
    if page is not None and page_size is None:
        raise ApiError(400, "page_size is required when page is provided")
    if page_size is not None:
        return stmt.offset(((page or 1) - 1) * page_size).limit(page_size)
    return stmt


@router.post("/")
async def register(body: RegisterIn, db: AsyncSession = Depends(get_db)):
    """公开注册。"""
    user = await create_user(db, body.username, body.password, role="user")
    return ok(await user_detail(db, user))


@router.post("/admin")
async def create_admin(body: RegisterIn, db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    """管理员创建新的管理员账号。"""
    user = await create_user(db, body.username, body.password, role="admin")
    return ok(user_public(user))


@router.get("/")
async def list_users(
    page: int | None = None,
    page_size: int | None = None,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    total = await db.scalar(select(func.count()).select_from(User)) or 0
    stmt = _apply_paging(select(User), page, page_size)
    users = (await db.scalars(stmt)).all()
    return ok({"total": total, "users": [await user_detail(db, u) for u in users]})


@router.get("/{user_id}")
async def get_user(user_id: int, db: AsyncSession = Depends(get_db), me: User = Depends(get_current_user)):
    """本人或管理员可查。"""
    if me.role != "admin" and me.id != user_id:
        raise ApiError(403, "permission denied")
    user = await db.get(User, user_id)
    if user is None:
        raise ApiError(404, "user not found")
    return ok(await user_detail(db, user))


@router.put("/{user_id}/role")
async def change_role(
    user_id: int, body: RoleIn, db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)
):
    if body.role not in VALID_ROLES:
        raise ApiError(400, "invalid role")
    user = await db.get(User, user_id)
    if user is None:
        raise ApiError(404, "user not found")
    user.role = body.role
    await db.commit()
    return ok({"user_id": user_id, "role": body.role})
