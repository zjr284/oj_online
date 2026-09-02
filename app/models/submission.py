"""评测相关模型：提交记录、测试点结果、语言注册表、访问审计（Step 2/3/5）。"""
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Submission(Base):
    """一次代码提交（Step 3）。

    status: pending(等待评测) / success(全部通过) / error(未通过或运行出错)
    counts: 各结果统计，如 {"AC": 8, "WA": 2}；评测完成前为 null
    """

    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    problem_id: Mapped[str] = mapped_column(String(64), index=True)
    language: Mapped[str] = mapped_column(String(32))
    code: Mapped[str] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    counts: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    compile_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_info: Mapped[str | None] = mapped_column(Text, nullable=True)

    submit_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class TestcaseResult(Base):
    """单个测试点的评测明细（Step 5）。

    result 取值：AC / WA / TLE / MLE / RE 等（与 FAQ 约定一致）。
    """

    __tablename__ = "testcase_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"), index=True)
    case_id: Mapped[str] = mapped_column(String(64))
    result: Mapped[str] = mapped_column(String(16))
    time: Mapped[float] = mapped_column(Float)
    memory: Mapped[float] = mapped_column(Float)


class Language(Base):
    """语言注册表（Step 2「动态注册语言」）。

    compile_cmd / run_cmd 含 {src} / {exe} 占位符，评测时替换为实际路径；
    time_limit / memory_limit 为空时使用题目限制。
    """

    __tablename__ = "languages"

    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    file_ext: Mapped[str] = mapped_column(String(16))
    compile_cmd: Mapped[str | None] = mapped_column(String(256), nullable=True)
    run_cmd: Mapped[str] = mapped_column(String(256))
    time_limit: Mapped[float | None] = mapped_column(Float, nullable=True)
    memory_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AccessLog(Base):
    """评测日志访问审计（Step 5）：记录谁在何时查看过哪道题的测试点明细。"""

    __tablename__ = "access_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    problem_id: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(32))   # 目前仅 view_logs
    status: Mapped[int] = mapped_column(Integer)      # 本次访问结果（HTTP 状态码）
    time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
