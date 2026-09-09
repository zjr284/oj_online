"""Step 3 评测管理接口（含 Step 5 的提交日志接口）。

接口与 api.md 一致：
- POST /api/submissions/                        提交（登录用户；429：1 分钟超 3 次）
- GET  /api/submissions/                        列表（本人或管理员；user_id/problem_id 一级条件，
                                                status/page/page_size 二级条件）
- GET  /api/submissions/{submission_id}         详情（本人或管理员）
- PUT  /api/submissions/{submission_id}/rejudge 重新评测（仅管理员，覆盖原记录）
- GET  /api/submissions/{submission_id}/log     测试点明细（Step 5，含可见性与访问审计）
"""
import json

from app.core.routing import AuthenticatedRoute

from fastapi import APIRouter, Depends
from sqlalchemy import delete, func, select
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

router = APIRouter(route_class=AuthenticatedRoute, prefix="/api/submissions", tags=["submissions"])

# 提交限流（api.md：1 分钟内超过 3 次 → 429；按用户计数）
submit_limiter = RateLimiter(config.SUBMIT_RATE_LIMIT, config.SUBMIT_RATE_WINDOW)


def _fmt_time(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _parse_info(raw: str | None) -> dict | str | None:
    """解析 compile_info / run_info：新数据为 JSON 对象字符串，解析回 dict。

    兼容旧记录（裸文本）与旧评测结果（原始字符串直接透传）。
    """
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


def _submission_item(sub: Submission) -> dict:
    # api.md：error/pending 状态只返回 id 和 status
    if sub.status in ("pending", "error"):
        return {"submission_id": str(sub.id), "status": sub.status}
    return {
        "submission_id": str(sub.id),
        "user_id": str(sub.user_id),
        "problem_id": sub.problem_id,
        "language": sub.language,
        "status": sub.status,
        "score": sub.score,
        "counts": sub.total_score,   # api.md：本题总分数（测试点数目 * 10）
        "verdicts": sub.counts,      # extra：各结果统计，供前端彩条展示
        "submit_time": _fmt_time(sub.submit_time),
    }


def _query_int(value: str | None, name: str, *, minimum: int | None = None) -> int | None:
    """在业务权限检查后解析查询整数，保证 403 优先于二级参数的 400。"""
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ApiError(400, f"invalid {name}")
    if minimum is not None and parsed < minimum:
        raise ApiError(400, f"invalid {name}")
    return parsed


@router.post("/")
async def create_submission(
    body: SubmissionIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    submit_limiter.check(str(user.id))   # 超限 → 429

    # 题目与语言存在性检查 → 404
    await store.get(body.problem_id)
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
    user_id: str | None = None,
    problem_id: str | None = None,
    status: str | None = None,
    page: str | None = None,
    page_size: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # 先解析一级身份条件并完成资源权限判断，再校验 page/status 等二级
    # 参数，以满足 api.md 的 403 > 400 异常优先级。
    requested_user_id = _query_int(user_id, "user_id")
    if (user.role != "admin" and requested_user_id is not None
            and requested_user_id != user.id):
        raise ApiError(403, "permission denied")

    page = _query_int(page, "page", minimum=1)
    page_size = _query_int(page_size, "page_size", minimum=1)
    if page is not None and page_size is None:
        raise ApiError(400, "page_size is required when page is provided")
    if requested_user_id is None and problem_id is None:
        raise ApiError(400, "at least one of user_id or problem_id is required")
    if status is not None and status not in ("pending", "success", "error"):
        raise ApiError(400, "invalid status")
    if user.role != "admin":
        requested_user_id = user.id

    conds = []
    if requested_user_id is not None:
        conds.append(Submission.user_id == requested_user_id)
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
        "user_id": str(sub.user_id),
        "problem_id": sub.problem_id,
        "language": sub.language,
        "status": sub.status,
        "score": sub.score,
        "counts": sub.total_score,   # api.md：本题总分数
        "verdicts": sub.counts,      # extra：各结果统计
        "compile_info": _parse_info(sub.compile_info),
        "run_info": _parse_info(sub.run_info),
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
    await judge_service.cancel_judge(sub.id)
    sub.status = "pending"
    sub.score = None
    sub.total_score = None
    sub.counts = None
    sub.compile_info = None
    sub.run_info = None
    sub.error_info = None
    await db.execute(delete(TestcaseResult).where(TestcaseResult.submission_id == sub.id))
    await db.commit()

    judge_service.schedule_judge(sub.id)
    return ok({"submission_id": str(sub.id), "status": "pending"}, msg="rejudge started")


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

    try:
        problem = await store.get(sub.problem_id)
        public = bool(problem.get("public_cases"))
    except ApiError as exc:
        if exc.status != 404:
            raise
        # 删除题目不会删除历史提交；缺少公开策略时默认关闭公开。
        public = False

    # 可见性：管理员 / 题目公开（所有登录用户）/ 本人
    if user.role != "admin" and not public and sub.user_id != user.id:
        await _record_access(db, user.id, sub.problem_id, 403)
        await db.commit()
        raise ApiError(403, "permission denied")

    data = {"score": sub.score, "counts": sub.total_score}
    # details：管理员始终可见；普通用户仅在题目 public_cases=True 时可见（未公开时省略该字段）
    if user.role == "admin" or public:
        rows = (await db.scalars(
            select(TestcaseResult)
            .where(TestcaseResult.submission_id == submission_id)
            .order_by(TestcaseResult.id)
        )).all()
        data["details"] = [
            {"id": int(r.case_id) if r.case_id.isdigit() else r.case_id,
             "result": r.result, "time": r.time, "memory": r.memory}
            for r in rows
        ]

    await _record_access(db, user.id, sub.problem_id, 200)
    await db.commit()
    return ok(data)
