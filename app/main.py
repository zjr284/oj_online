"""应用入口：装配路由、生命周期与静态页面。

启动方式：
    uvicorn app.main:app --reload
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import config
from app.core.errors import register_exception_handlers
from app.database import init_db
from app.routers import auth, languages, logs, maintenance, problems, submissions, users
from app.services import judge_service
from app.services.language_service import ensure_languages
from app.services.user_service import ensure_admin


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时：建表、创建数据目录、初始管理员与默认语言
    await init_db()
    config.PROBLEMS_DIR.mkdir(parents=True, exist_ok=True)
    await ensure_admin()
    await ensure_languages()
    # 重启恢复：重新评测遗留的 pending 提交
    await judge_service.requeue_pending()
    yield


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

# TODO(Advance)：实现后取消注释
# app.include_router(ai.router)

# 前端静态页面（挂在最后，避免覆盖 /api 路由）
app.mount("/", StaticFiles(directory=config.BASE_DIR / "static", html=True), name="static")
