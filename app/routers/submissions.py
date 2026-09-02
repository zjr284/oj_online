"""Step 3 评测管理接口（含 Step 5 的提交日志接口）。

接口与 api.md 一致：
- POST /api/submissions/                        提交（登录用户；429：1 分钟超 3 次）
- GET  /api/submissions/                        列表（本人或管理员；user_id/problem_id 一级条件，
                                                status/page/page_size 二级条件）
- GET  /api/submissions/{submission_id}         详情（本人或管理员）
- PUT  /api/submissions/{submission_id}/rejudge 重新评测（仅管理员，覆盖原记录）
- GET  /api/submissions/{submission_id}/log     测试点明细（Step 5，含可见性与访问审计）
"""
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.core.deps import get_current_user, require_admin
from app.core.errors import ApiError, ok
from app.core.rate_limit import RateLimiter
from app.database import get_db
from app.models import AccessLog, Language, Submission, TestcaseResult, User
from app.schemas.submission import SubmissionIn
from app.services import judge_service
from app.services.problem_store import store

router = APIRouter(prefix="/api/submissions", tags=["submissions"])

# 提交限流（api.md：1 分钟内超过 3 次 → 429；按用户计数）
submit_limiter = RateLimiter(config.SUBMIT_RATE_LIMIT, config.SUBMIT_RATE_WINDOW)


def _fmt_time(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _submission_item(sub: Submission) -> dict:
    # api.md：error/pending 状态只返回 id 和 status
    if sub.status in ("pending", "error"):
        return {"submission_id": str(sub.id), "status": sub.status}
    return {
        "submission_id": str(sub.id),
        "user_id": sub.user_id,
        "problem_id": sub.problem_id,
        "language": sub.language,
        "status": sub.status,
        "score": sub.score,
        "counts": sub.counts,
        "submit_time": _fmt_time(sub.submit_time),
    }


@router.post("/")
async def create_submission(
    body: SubmissionIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    submit_limiter.check(str(user.id))   # 超限 → 429

    # 题目与语言存在性检查 → 404
    try:
        await store.get(body.problem_id)
    except ApiError as e:
        raise ApiError(404, "problem not found") from e
    if await db.get(Language, body.language) is None:
        raise ApiError(404, "language not found")

    sub = Submission(
        user_id=user.id, problem_id=body.problem_id,
        language=body.language, code=body.code, status="pending",
    )
    db.add(sub)
    await db.commit()
    await db.refresh(sub)

    judge_service.schedule_judge(sub.id)   # 异步评测
    return ok({"submission_id": str(sub.id), "status": "pending"})


@router.get("/")
async def list_submissions(
    user_id: int | None = None,
    problem_id: str | None = None,
    status: str | None = None,
    page: int | None = None,
    page_size: int | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if page is not None and page_size is None:
        raise ApiError(400, "page_size is required when page is provided")

    # 权限：普通用户只能查自己的记录
    if user.role != "admin":
        if user_id is not None and user_id != user.id:
            raise ApiError(403, "permission denied")
        user_id = user.id

    conds = []
    if user_id is not None:
        conds.append(Submission.user_id == user_id)
    if problem_id is not None:
        conds.append(Submission.problem_id == problem_id)
    if status is not None:
        conds.append(Submission.status == status)

    total = await db.scalar(select(func.count()).select_from(Submission).where(*conds)) or 0
    stmt = select(Submission).where(*conds).order_by(Submission.id.desc())
    if page_size is not None:
        stmt = stmt.offset(((page or 1) - 1) * page_size).limit(page_size)
    subs = (await db.scalars(stmt)).all()

    return ok({"total": total, "submissions": [_submission_item(s) for s in subs]})


@router.get("/{submission_id}")
async def get_submission(
    submission_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    sub = await db.get(Submission, submission_id)
    if sub is None:
        raise ApiError(404, "submission not found")
    if user.role != "admin" and sub.user_id != user.id:
        raise ApiError(403, "permission denied")
    return ok({
        "submission_id": str(sub.id),
        "user_id": sub.user_id,
        "problem_id": sub.problem_id,
        "language": sub.language,
        "status": sub.status,
        "score": sub.score,
        "counts": sub.counts,
        "compile_info": sub.compile_info,
        "run_info": sub.run_info,
        "error_info": sub.error_info,
        "code": sub.code,
        "submit_time": _fmt_time(sub.submit_time),
    })


@router.put("/{submission_id}/rejudge")
async def rejudge_submission(
    submission_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    sub = await db.get(Submission, submission_id)
    if sub is None:
        raise ApiError(404, "submission not found")

    # 覆盖原记录：取消旧任务 → 重置为 pending → 重新评测
    judge_service.cancel_judge(sub.id)
    sub.status = "pending"
    sub.score = None
    sub.counts = None
    sub.compile_info = None
    sub.run_info = None
    sub.error_info = None
    await db.commit()

    judge_service.schedule_judge(sub.id)
    return ok({"submission_id": str(sub.id), "status": "pending"})


# ---- Step 5：评测日志 ----


async def _record_access(db: AsyncSession, user_id: int, problem_id: str, status: int) -> None:
    db.add(AccessLog(user_id=user_id, problem_id=problem_id, action="view_logs", status=status))


@router.get("/{submission_id}/log")
async def get_submission_log(
    submission_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    sub = await db.get(Submission, submission_id)
    if sub is None:
        raise ApiError(404, "submission not found")   # api.md：评测不存在不记录审计

    problem = await store.get(sub.problem_id)
    public = bool(problem.get("public_cases"))

    # 可见性：管理员 / 题目公开（所有登录用户）/ 本人
    if user.role != "admin" and not public and sub.user_id != user.id:
        await _record_access(db, user.id, sub.problem_id, 403)
        await db.commit()
        raise ApiError(403, "permission denied")

    data = {"score": sub.score, "counts": sub.counts}
    # details：管理员始终可见；普通用户仅在题目 public_cases=True 时可见（未公开时省略该字段）
    if user.role == "admin" or public:
        rows = (await db.scalars(
            select(TestcaseResult)
            .where(TestcaseResult.submission_id == submission_id)
            .order_by(TestcaseResult.id)
        )).all()
        data["details"] = [
            {"id": r.case_id, "result": r.result, "time": r.time, "memory": r.memory}
            for r in rows
        ]

    await _record_access(db, user.id, sub.problem_id, 200)
    await db.commit()
    return ok(data)
