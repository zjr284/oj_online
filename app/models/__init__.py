"""ORM 模型统一出口：init_db 依赖此处的导入来注册全部模型。"""
from app.models.ai import AiTask
from app.models.submission import AccessLog, Language, Submission, TestcaseResult
from app.models.user import Session, User

__all__ = [
    "AccessLog",
    "AiTask",
    "Language",
    "Session",
    "Submission",
    "TestcaseResult",
    "User",
]
