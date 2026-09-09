"""Step 4 用户管理接口。

分页语义（api.md）：page 与 page_size 全空 = 查全部；
page 空 page_size 非空 = 第一页；page 非空 page_size 空 = 参数错误。
列表按 submit_count 降序（并列按 user_id 升序），与 api.md 示例一致。
"""
from app.core.routing import AuthenticatedRoute

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, require_admin
from app.core.errors import ApiError, ok
from app.core.pagination import MAX_PAGE, MAX_PAGE_SIZE
from app.database import get_db
from app.models import RoleChangeLog, Submission, User
from app.schemas.user import RegisterIn, RoleIn, UsernameIn
from app.services.user_service import create_user, rename_user, user_detail, user_public

router = APIRouter(route_class=AuthenticatedRoute, prefix="/api/users", tags=["users"])

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
    return ok(await user_detail(db, user), msg="register success")


@router.post("/admin")
async def create_admin(body: RegisterIn, db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    """管理员创建新的管理员账号（记录操作日志）。"""
    user = await create_user(db, body.username, body.password, role="admin")
    db.add(RoleChangeLog(operator_id=admin.id, target_id=user.id, old_role="-", new_role="admin"))
    await db.commit()
    return ok(user_public(user))


@router.get("/")
async def list_users(
    page: int | None = Query(None, ge=1, le=MAX_PAGE),
    page_size: int | None = Query(None, ge=1, le=MAX_PAGE_SIZE),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """管理员分页查看所有用户及其提交统计。"""
    total = await db.scalar(select(func.count()).select_from(User)) or 0
    # api.md 示例按 submit_count 降序排列（100 / 90 / 80）；并列时按 user_id 升序保证翻页稳定
    submit_cnt = (
        select(func.count(Submission.id))
        .where(Submission.user_id == User.id)
        .correlate(User)
        .scalar_subquery()
    )
    stmt = _apply_paging(select(User).order_by(submit_cnt.desc(), User.id), page, page_size)
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
    """管理员变更目标用户角色，并记录角色变更日志。"""
    if body.role not in VALID_ROLES:
        raise ApiError(400, "invalid role")
    user = await db.get(User, user_id)
    if user is None:
        raise ApiError(404, "user not found")
    # 记录操作日志（Step 4：谁在何时修改了谁的权限）
    db.add(RoleChangeLog(operator_id=admin.id, target_id=user.id, old_role=user.role, new_role=body.role))
    user.role = body.role
    await db.commit()
    return ok({"user_id": str(user_id), "role": body.role}, msg="role updated")


@router.put("/{user_id}/username")
async def change_username(
    body: UsernameIn,
    user_id: int,
    db: AsyncSession = Depends(get_db),
    me: User = Depends(get_current_user),
):
    """用户修改自己的用户名。管理员也只能通过本接口修改本人。"""
    if me.id != user_id:
        raise ApiError(403, "permission denied")
    user = await rename_user(db, me, body.username.strip())
    return ok({"user_id": str(user.id), "username": user.username}, msg="username updated")
