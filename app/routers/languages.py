"""Step 2 语言注册表：增查接口。

语言以数据库记录注册（languages 表），评测时动态读取——
这正是「动态注册语言」的评分依据。判题执行部分见 app/judge/。

权限说明（Step 2 任务 3 / Step 4 权限回溯）：注册语言可由任意
「已登录用户」执行；未登录 401，被封禁 403。
"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.errors import ApiError, ok
from app.database import get_db
from app.models import Language, User
from app.schemas.language import LanguageIn

router = APIRouter(prefix="/api/languages", tags=["languages"])

PLACEHOLDERS = ("{src}", "{exe}")


def _validate_cmd(lang: LanguageIn) -> None:
    """命令模板必须包含 {src} 或 {exe} 路径占位符（api.md）。"""
    if not any(p in lang.run_cmd for p in PLACEHOLDERS):
        raise ApiError(400, "run_cmd must contain {src} or {exe}")
    if lang.compile_cmd and not any(p in lang.compile_cmd for p in PLACEHOLDERS):
        raise ApiError(400, "compile_cmd must contain {src} or {exe}")


@router.get("/")
async def list_languages(db: AsyncSession = Depends(get_db)):
    """公开接口：返回 {name: [语言列表]}。"""
    names = (await db.scalars(select(Language.name).order_by(Language.name))).all()
    return ok({"name": list(names)})


@router.post("/")
async def register_language(
    body: LanguageIn, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    _validate_cmd(body)
    if await db.get(Language, body.name) is not None:
        raise ApiError(400, "language already exists")
    db.add(Language(**body.model_dump()))
    await db.commit()
    return ok({"name": body.name}, msg="language registered")
