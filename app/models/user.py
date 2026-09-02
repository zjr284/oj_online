"""用户与会话模型（Step 4）。"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class User(Base):
    """用户。

    role: user（普通用户）/ admin（管理员）/ banned（封禁）
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(16), default="user")
    join_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Session(Base):
    """登录会话（Cookie 中仅存 token，服务端可随时吊销）。"""

    __tablename__ = "sessions"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class RoleChangeLog(Base):
    """权限变更操作日志（Step 4：谁在何时修改了谁的权限）。"""

    __tablename__ = "role_change_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operator_id: Mapped[int] = mapped_column(Integer, index=True)
    target_id: Mapped[int] = mapped_column(Integer, index=True)
    old_role: Mapped[str] = mapped_column(String(16))
    new_role: Mapped[str] = mapped_column(String(16))
    time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
