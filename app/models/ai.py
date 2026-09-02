"""AI 智能命题任务模型（Advance 进阶模块，预留）。

api.md 安全要求：模型密钥不得存明文、不得通过接口返回；
本表只记录 provider_url / model 等非敏感信息，密钥单独加密存储或存环境变量。
"""
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AiTask(Base):
    __tablename__ = "ai_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    requirement: Mapped[str] = mapped_column(Text)              # 命题需求描述
    problem_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 参考/修改的已有题目

    # pending(等待) / running(执行) / done(完成) / cancelled(已取消) / failed(失败)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 生成的题目配置
    usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)   # token 用量与费用
    provider_url: Mapped[str | None] = mapped_column(String(256), nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
