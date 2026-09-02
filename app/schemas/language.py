"""语言注册请求模型（Step 2）。

安全（Step 2 评分点「配置安全」）：name/file_ext 限安全字符集，
避免构造异常文件名或破坏命令模板；时间/内存限制必须为正。
"""
from pydantic import BaseModel, Field

# 语言名仅字母数字与 ._-：既是文件名片段，也会出现在日志与命令中
NAME_RE = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$"
# 扩展名形如 .py / .cpp（点可选，评测时自动补点；杜绝路径分隔符与隐藏文件前缀）
EXT_RE = r"^\.?[A-Za-z0-9]{1,10}$"


class LanguageIn(BaseModel):
    name: str = Field(pattern=NAME_RE)
    file_ext: str = Field(pattern=EXT_RE)
    compile_cmd: str | None = Field(default=None, max_length=256)
    run_cmd: str = Field(max_length=256)
    time_limit: float | None = Field(default=None, gt=0)    # 缺省使用题目限制
    memory_limit: int | None = Field(default=None, gt=0)
