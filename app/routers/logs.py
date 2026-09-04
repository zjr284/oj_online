"""Step 5 访问审计接口：GET /api/logs/access/ 仅管理员。

筛选参数：user_id（str，api.md）、problem_id、page、page_size（分页语义同 submissions 列表）。
action 仅为 view_logs；status 记录访问结果（200 允许 / 403 拒绝）。
不记录：未登录、评测不存在、参数错误的访问（见 submissions.py 的 log 接口）。
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_admin
from app.core.errors import ApiError, ok
from app.database import get_db
from app.models import AccessLog, User

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("/access/")
async def list_access_logs(
    user_id: str | None = None,   # api.md：user_id 为 str；SQLite 数值列与数字串比较自动匹配
    problem_id: str | None = None,
    page: int | None = Query(None, ge=1),
    page_size: int | None = Query(None, ge=1),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if page is not None and page_size is None:
        raise ApiError(400, "page_size is required when page is provided")

    conds = []
    if user_id is not None:
        conds.append(AccessLog.user_id == user_id)
    if problem_id is not None:
        conds.append(AccessLog.problem_id == problem_id)

    stmt = select(AccessLog).where(*conds).order_by(AccessLog.id.desc())
    if page_size is not None:
        stmt = stmt.offset(((page or 1) - 1) * page_size).limit(page_size)
    rows = (await db.scalars(stmt)).all()

    # api.md：直接返回数组；示例中 user_id / status 均为字符串
    return ok([
        {
            "user_id": str(r.user_id),
            "problem_id": r.problem_id,
            "action": r.action,
            "time": r.time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": str(r.status),
        }
        for r in rows
    ])
