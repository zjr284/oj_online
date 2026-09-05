"""题目相关请求模型（Step 1）。

字段与 api.md 一致：8 个必填字段 + 可选字段。
可选字段缺失时自动填充默认值（str → ""，list → []），
与「默认字段返回类型默认值」的要求对应。

安全：id 限制为安全字符集（杜绝经请求体 id 的路径穿越写入/删除任意 .json 文件）；
time/memory 限制必须为正数。
"""
from pydantic import BaseModel, Field

# 题目 id 只能由字母数字与 _- 组成：既保证文件名安全（不能含 / 或 ..），也符合 api.md 示例
PROBLEM_ID_RE = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"


class Sample(BaseModel):
    input: str
    output: str


class TestCase(BaseModel):
    id: str | None = None   # 测试点编号（可选，缺省按数组顺序编号）
    input: str
    output: str


class ProblemConfig(BaseModel):
    # ---- 必填字段 ----
    id: str = Field(pattern=PROBLEM_ID_RE, description="题目唯一标识（字母数字与 _- 组成）")
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    input_description: str = Field(min_length=1)
    output_description: str = Field(min_length=1)
    samples: list[Sample] = Field(min_length=1)
    constraints: str = Field(min_length=1)
    testcases: list[TestCase] = Field(min_length=1)

    # ---- 可选字段（带默认值）----
    hint: str = ""
    source: str = ""
    tags: list[str] = []
    time_limit: float = Field(default=3.0, gt=0, allow_inf_nan=False)
    memory_limit: int = Field(default=128, gt=0)
    author: str = ""
    difficulty: str = ""
    public_cases: bool = False   # Step 5：测试点明细是否对普通用户可见


class LogVisibilityIn(BaseModel):
    public_cases: bool = False
