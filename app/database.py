"""数据库访问层：SQLAlchemy 2.0 异步引擎与会话。

当前使用 SQLite（aiosqlite 驱动），验收环境开箱即用；
如需切换 PostgreSQL/MySQL，只需修改 config.DB_URL 并安装对应异步驱动，
业务代码（models/services/routers）不受影响。
"""
import asyncio

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app import config


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


engine = create_async_engine(config.DB_URL, echo=False)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    """建表并补齐轻量字段迁移（均幂等，可重复调用）。"""
    from app import models  # noqa: F401  确保所有模型已注册到 Base.metadata

    await asyncio.to_thread(config.DATA_DIR.mkdir, parents=True, exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_columns)


def _migrate_columns(conn) -> None:
    """为没有迁移框架的既有部署补齐向后兼容的可空字段。"""
    tables = set(inspect(conn).get_table_names())
    if "ai_tasks" not in tables:
        return
    columns = {column["name"] for column in inspect(conn).get_columns("ai_tasks")}
    if "generation_mode" not in columns:
        conn.execute(text("ALTER TABLE ai_tasks ADD COLUMN generation_mode VARCHAR(16)"))
    if "parent_task_id" not in columns:
        conn.execute(text("ALTER TABLE ai_tasks ADD COLUMN parent_task_id INTEGER"))


async def get_db():
    """FastAPI 依赖：为每个请求提供一个独立数据库会话。"""
    async with SessionLocal() as session:
        yield session
