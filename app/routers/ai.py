"""Advance 进阶模块：AI 智能命题接口。

接口规格（api.md「AI 智能命题接口」，允许等价设计并在文档说明）：
- PUT  /api/ai/model-config                 配置模型（登录用户；api_key 加密存储，永不返回）
- GET  /api/ai/model-config                 读取配置的公开字段（等价扩展：api_key 仍不返回）
- POST /api/ai/problem-tasks/               创建命题任务（登录用户；无配置 400、题目不存在 404）
- GET  /api/ai/problem-tasks/{task_id}          任务状态（创建者或管理员）
- GET  /api/ai/problem-tasks/{task_id}/events   SSE 进度事件（创建者或管理员）
- PUT  /api/ai/problem-tasks/{task_id}/cancel   取消任务（真正终止后台执行；已结束 409）

安全要求（api.md）：api_key 不得经任何接口返回；费用公式与用量统计见
app/services/ai_service.py（模型不返回用量时按字符数估算并标注 estimated）。
"""
from app.core.routing import AuthenticatedRoute

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.core.deps import get_current_user
from app.core.errors import ApiError, ok
from app.models import User
from app.schemas.ai import AiTaskIn, ModelConfigIn
from app.services import ai_service

router = APIRouter(route_class=AuthenticatedRoute, prefix="/api/ai", tags=["ai"])


@router.put("/model-config")
async def set_model_config(body: ModelConfigIn, user: User = Depends(get_current_user)):
    """配置模型提供商；返回不含 api_key 的公开字段（api.md）。"""
    if not body.provider_url.startswith(("http://", "https://")):
        raise ApiError(400, "provider_url must be http(s)")
    if (body.input_price or 0) < 0 or (body.output_price or 0) < 0:
        raise ApiError(400, "price must be non-negative")
    data = await ai_service.config_store.save(body)
    return ok(data, msg="model config updated")


@router.get("/model-config")
async def get_model_config(user: User = Depends(get_current_user)):
    """读取配置的公开字段（不返回 api_key）；未配置返回 api_key_configured: false。"""
    cfg = await ai_service.config_store.load()
    if cfg is None:
        return ok({"api_key_configured": False})
    return ok({
        "provider_url": cfg["provider_url"],
        "model": cfg["model"],
        "input_price": cfg["input_price"],
        "output_price": cfg["output_price"],
        "price_unit": cfg["price_unit"],
        "currency": cfg.get("currency", "CNY"),
        "api_key_configured": True,
    })


@router.post("/problem-tasks/")
async def create_problem_task(body: AiTaskIn, user: User = Depends(get_current_user)):
    task = await ai_service.create_task(user, body)
    return ok({"task_id": task.id, "status": task.status}, msg="task created")


@router.get("/problem-tasks/")
async def list_problem_tasks(user: User = Depends(get_current_user)):
    """普通用户查询本人任务，管理员查询全部；列表项不含 result。"""
    return ok(await ai_service.list_tasks(user))


@router.get("/problem-tasks/{task_id}")
async def get_problem_task(task_id: int, user: User = Depends(get_current_user)):
    return ok(await ai_service.get_task(user, task_id))


@router.get("/problem-tasks/{task_id}/events")
async def get_problem_task_events(task_id: int, user: User = Depends(get_current_user)):
    """SSE 实时进度（api.md 允许等价方案；同时支持轮询 GET 状态接口）。

    权限/存在性校验在响应开始前完成（401/403/404），
    流开始后不再抛业务异常。
    """
    state = await ai_service.get_task(user, task_id)
    return StreamingResponse(
        ai_service.sse_stream(state, task_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.put("/problem-tasks/{task_id}/cancel")
async def cancel_problem_task(task_id: int, user: User = Depends(get_current_user)):
    status = await ai_service.cancel_task(user, task_id)
    return ok({"task_id": task_id, "status": status}, msg="task cancelled")
