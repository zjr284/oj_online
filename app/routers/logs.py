"""Step 5 访问审计接口：管理员查询和删除访问日志。

筛选参数：username、user_id（保留兼容）、problem_id、page、page_size。
action 仅为 view_logs；status 记录访问结果（200 允许 / 403 拒绝）。
不记录：未登录、评测不存在、参数错误的访问（见 submissions.py 的 log 接口）。
"""
from app.core.routing import AuthenticatedRoute

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_admin
from app.core.errors import ApiError, ok
from app.core.pagination import MAX_PAGE, MAX_PAGE_SIZE
from app.database import get_db
from app.models import AccessLog, User

router = APIRouter(route_class=AuthenticatedRoute, prefix="/api/logs", tags=["logs"])


@router.get("/access/")
async def list_access_logs(
    user_id: str | None = None,   # api.md：user_id 为 str；SQLite 数值列与数字串比较自动匹配
    username: str | None = None,
    problem_id: str | None = None,
    page: int | None = Query(None, ge=1, le=MAX_PAGE),
    page_size: int | None = Query(None, ge=1, le=MAX_PAGE_SIZE),
    include_total: bool = False,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """管理员分页查询访问审计记录，并联表显示用户名。"""
    if page is not None and page_size is None:
        raise ApiError(400, "page_size is required when page is provided")

    conds = []
    if user_id is not None:
        conds.append(AccessLog.user_id == user_id)
    if username is not None:
        conds.append(User.username == username)
    if problem_id is not None:
        conds.append(AccessLog.problem_id == problem_id)

    total = None
    if include_total:
        total = await db.scalar(
            select(func.count()).select_from(AccessLog).outerjoin(
                User, User.id == AccessLog.user_id,
            ).where(*conds)
        ) or 0
    stmt = (
        select(AccessLog, User.username)
        .outerjoin(User, User.id == AccessLog.user_id)
        .where(*conds)
        .order_by(AccessLog.id.desc())
    )
    if page_size is not None:
        stmt = stmt.offset(((page or 1) - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).all()

    # 保留 user_id 兼容原接口；前端使用 username 展示审计主体。
    logs = [
        {
            "log_id": str(log.id),
            "username": username or "已删除用户",
            "user_id": str(log.user_id),
            "problem_id": log.problem_id,
            "action": log.action,
            "time": log.time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": str(log.status),
        }
        for log, username in rows
    ]
    # 默认仍返回 api.md 规定的数组；前端显式请求总数以计算总页数。
    return ok({"total": total, "logs": logs} if include_total else logs)


@router.delete("/access/{log_id}")
async def delete_access_log(
    log_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """删除单条访问审计日志；仅管理员可操作。"""
    log = await db.get(AccessLog, log_id)
    if log is None:
        raise ApiError(404, "access log not found")
    await db.delete(log)
    await db.commit()
    return ok({"log_id": str(log_id)}, msg="access log deleted")
