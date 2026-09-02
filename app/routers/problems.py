"""Step 1 题目管理接口（含 Step 5 的日志可见性设置）。

接口与 api.md 一致：
- GET    /api/problems/                       题目列表（登录用户）
- POST   /api/problems/                       添加题目（登录用户，id 冲突 409）
- GET    /api/problems/{problem_id}           题目详情（登录用户）
- PUT    /api/problems/{problem_id}           覆盖更新（登录用户，body.id 须与路径一致）
- DELETE /api/problems/{problem_id}           删除（仅管理员）
- PUT    /api/problems/{problem_id}/log_visibility  测试点可见性（仅管理员，Step 5）
"""
from fastapi import APIRouter, Depends

from app.core.deps import get_current_user, require_admin
from app.core.errors import ApiError, ok
from app.models import User
from app.schemas.problem import LogVisibilityIn, ProblemConfig
from app.services.problem_store import store

router = APIRouter(prefix="/api/problems", tags=["problems"])


@router.get("/")
async def list_problems(user: User = Depends(get_current_user)):
    return ok(await store.list_problems())


@router.post("/")
async def create_problem(cfg: ProblemConfig, user: User = Depends(get_current_user)):
    await store.create(cfg)
    return ok({"id": cfg.id})


@router.get("/{problem_id}")
async def get_problem(problem_id: str, user: User = Depends(get_current_user)):
    return ok(await store.get(problem_id))


@router.put("/{problem_id}")
async def update_problem(problem_id: str, cfg: ProblemConfig, user: User = Depends(get_current_user)):
    if cfg.id != problem_id:
        raise ApiError(400, "body id must match path id")
    await store.update(cfg)
    return ok({"id": problem_id})


@router.delete("/{problem_id}")
async def delete_problem(problem_id: str, admin: User = Depends(require_admin)):
    await store.delete(problem_id)
    return ok({"id": problem_id})


@router.put("/{problem_id}/log_visibility")
async def set_log_visibility(
    problem_id: str, body: LogVisibilityIn, admin: User = Depends(require_admin)
):
    await store.set_public_cases(problem_id, body.public_cases)
    return ok({"problem_id": problem_id, "public_cases": body.public_cases})
