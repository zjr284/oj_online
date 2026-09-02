"""语言服务：注册表维护（Step 2）。"""
from sqlalchemy import select

from app.database import SessionLocal
from app.models import Language

# 默认语言（启动时自动注册；评测命令模板含 {src}/{exe} 占位符）
DEFAULT_LANGUAGES = [
    {"name": "python", "file_ext": "py", "compile_cmd": None, "run_cmd": "python3 {src}"},
    {"name": "cpp", "file_ext": "cpp", "compile_cmd": "g++ -O2 -std=c++17 {src} -o {exe}", "run_cmd": "{exe}"},
]


async def ensure_languages() -> None:
    """启动时注册默认语言（已存在则跳过）。"""
    async with SessionLocal() as db:
        for cfg in DEFAULT_LANGUAGES:
            if await db.get(Language, cfg["name"]) is None:
                db.add(Language(**cfg))
        await db.commit()
