"""应用入口：装配路由与生命周期（纯 API 服务，前端为 Streamlit app.py）。

启动方式：
    uvicorn app.main:app --reload
"""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import config
from app.core.errors import register_exception_handlers
from app.database import init_db
from app.routers import ai, auth, languages, logs, maintenance, problems, submissions, users
from app.services import ai_service, judge_service
from app.services.language_service import ensure_languages
from app.services.problem_migration import migrate_problem_references
from app.services.problem_store import store
from app.services.user_service import ensure_admin


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时：建表、创建数据目录、初始管理员与默认语言
    await init_db()
    await asyncio.to_thread(config.PROBLEMS_DIR.mkdir, parents=True, exist_ok=True)
    await migrate_problem_references(await store.migrate_safe_ids())
    await ensure_admin()
    await ensure_languages()
    await judge_service.normalize_legacy_results()
    # 重启恢复：重新评测遗留的 pending 提交
    await judge_service.requeue_pending()
    # AI 命题任务兜底：进程重启后遗留的 pending/running 标记为 failed
    await ai_service.fail_stale_tasks()
    try:
        yield
    finally:
        await judge_service.shutdown()
        await ai_service.shutdown()


app = FastAPI(title="Online Judge", lifespan=lifespan)

# 统一异常处理：所有错误返回 {code, msg, data} 格式（api.md）
register_exception_handlers(app)

# 业务路由
app.include_router(problems.router)     # Step 1 题目管理（+ Step 5 可见性）
app.include_router(auth.router)         # Step 4 登录/登出
app.include_router(users.router)        # Step 4 用户管理
app.include_router(languages.router)    # Step 2 语言注册表
app.include_router(submissions.router)  # Step 3 评测管理（+ Step 5 提交日志）
app.include_router(logs.router)         # Step 5 访问审计
app.include_router(maintenance.router)  # 测试辅助 /api/reset/
app.include_router(ai.router)           # Advance：AI 智能命题
