"""数据库访问层：SQLAlchemy 2.0 异步引擎与会话。

当前使用 SQLite（aiosqlite 驱动），验收环境开箱即用；
如需切换 PostgreSQL/MySQL，只需修改 config.DB_URL 并安装对应异步驱动，
业务代码（models/services/routers）不受影响。
"""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app import config


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


engine = create_async_engine(config.DB_URL, echo=False)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    """建表（create_all 幂等，可重复调用）。"""
    from app import models  # noqa: F401  确保所有模型已注册到 Base.metadata

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    """FastAPI 依赖：为每个请求提供一个独立数据库会话。"""
    async with SessionLocal() as session:
        yield session
