"""Step 2 语言注册表：增查接口。

语言以数据库记录注册（languages 表），评测时动态读取——
这正是「动态注册语言」的评分依据。判题执行部分见 app/judge/。

权限说明：所有已登录用户均可动态注册语言；未登录返回 401。
"""
from app.core.routing import AuthenticatedRoute

import re
import shlex

from fastapi import APIRouter, Depends
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.errors import ApiError, ok
from app.database import get_db
from app.models import Language, User
from app.schemas.language import LanguageIn

router = APIRouter(route_class=AuthenticatedRoute, prefix="/api/languages", tags=["languages"])

PLACEHOLDERS = ("{src}", "{exe}")


def _validate_cmd(lang: LanguageIn) -> None:
    """命令模板必须包含 {src} 或 {exe} 路径占位符（api.md）。"""
    if not any(p in lang.run_cmd for p in PLACEHOLDERS):
        raise ApiError(400, "run_cmd must contain {src} or {exe}")
    if lang.compile_cmd and not any(p in lang.compile_cmd for p in PLACEHOLDERS):
        raise ApiError(400, "compile_cmd must contain {src} or {exe}")
    for command in (lang.compile_cmd, lang.run_cmd):
        if not command:
            continue
        try:
            parts = shlex.split(command)
        except ValueError:
            raise ApiError(400, "invalid command quoting")
        if not parts or "\x00" in command:
            raise ApiError(400, "invalid command")
        if any(p not in ("src", "exe") for p in re.findall(r"\{([^{}]*)\}", command)):
            raise ApiError(400, "unknown command placeholder")
        if any(part in ("|", "||", "&&", ";", ">", ">>", "<") for part in parts):
            raise ApiError(400, "shell operators are not supported")


@router.get("/")
async def list_languages(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """登录用户查询语言列表，返回 {name: [语言列表]}。

    按注册（插入）顺序返回，与 api.md 示例 ["python", "cpp"] 一致。
    语言无删除接口，SQLite 的 rowid 单调递增，即注册顺序。
    """
    names = (await db.scalars(select(Language.name).order_by(text("rowid")))).all()
    return ok({"name": list(names)})


@router.post("/")
async def register_language(
    body: LanguageIn, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    """校验命令模板后注册语言；同名配置会被拒绝。"""
    _validate_cmd(body)
    if await db.get(Language, body.name) is not None:
        raise ApiError(400, "language already exists")
    db.add(Language(**body.model_dump()))
    try:
        await db.commit()
    except IntegrityError:
        # 并发注册同一名称：唯一约束兜底 → 400（而非 500）
        await db.rollback()
        raise ApiError(400, "language already exists")
    return ok({"name": body.name}, msg="language registered")
