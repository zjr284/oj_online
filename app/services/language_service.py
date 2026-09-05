"""语言服务：注册表维护（Step 2）。"""
from app.database import SessionLocal
from app.models import Language

# 默认语言（启动时自动注册；评测命令模板含 {src}/{exe} 占位符）
DEFAULT_LANGUAGES = [
    {"name": "python", "file_ext": ".py", "compile_cmd": None, "run_cmd": "python3 {src}"},
    {"name": "cpp", "file_ext": ".cpp", "compile_cmd": "g++ -O2 -std=c++14 {src} -o {exe}", "run_cmd": "{exe}"},
]


async def ensure_languages() -> None:
    """启动时注册默认语言（已存在则同步到规范配置）。"""
    async with SessionLocal() as db:
        for cfg in DEFAULT_LANGUAGES:
            row = await db.get(Language, cfg["name"])
            if row is None:
                db.add(Language(**cfg))
            else:
                # 内置语言由系统维护。同步执行配置可修复旧数据库中的
                # 过期命令（例如曾使用 C++17 的 cpp 配置）。
                for field in ("file_ext", "compile_cmd", "run_cmd"):
                    setattr(row, field, cfg[field])
        await db.commit()
