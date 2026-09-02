"""语言注册请求模型（Step 2）。"""
from pydantic import BaseModel


class LanguageIn(BaseModel):
    name: str
    file_ext: str
    compile_cmd: str | None = None
    run_cmd: str
    time_limit: float | None = None    # 缺省使用题目限制
    memory_limit: int | None = None
