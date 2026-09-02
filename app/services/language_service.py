"""语言服务：注册表维护（Step 2）。"""
from sqlalchemy import select

from app.database import SessionLocal
from app.models import Language

# 默认语言（启动时自动注册；评测命令模板含 {src}/{exe} 占位符）
DEFAULT_LANGUAGES = [
    {"name": "python", "file_ext": ".py", "compile_cmd": None, "run_cmd": "python3 {src}"},
    {"name": "cpp", "file_ext": ".cpp", "compile_cmd": "g++ -O2 -std=c++17 {src} -o {exe}", "run_cmd": "{exe}"},
]


async def ensure_languages() -> None:
    """启动时注册默认语言（已存在则同步到规范配置）。"""
    async with SessionLocal() as db:
        for cfg in DEFAULT_LANGUAGES:
            row = await db.get(Language, cfg["name"])
            if row is None:
                db.add(Language(**cfg))
            elif row.file_ext != cfg["file_ext"]:
                row.file_ext = cfg["file_ext"]
        await db.commit()
