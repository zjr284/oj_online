"""题目相关请求模型（Step 1）。

字段与 api.md 一致：8 个必填字段 + 可选字段。
可选字段缺失时自动填充默认值（str → ""，list → []），
与「默认字段返回类型默认值」的要求对应。

安全：id 统一限制为十进制数字（同时杜绝经请求体 id 的路径穿越）；
time/memory 限制必须为正数。
"""
from pydantic import BaseModel, Field

# 题目 id 统一为 1–18 位十进制数字，作为安全的 JSON 文件名。
PROBLEM_ID_RE = r"^[0-9]{1,18}$"


class Sample(BaseModel):
    input: str
    output: str


class TestCase(BaseModel):
    id: str | None = None   # 测试点编号（可选，缺省按数组顺序编号）
    input: str
    output: str


class ProblemConfig(BaseModel):
    # ---- 必填字段 ----
    id: str = Field(pattern=PROBLEM_ID_RE, description="题目唯一数字标识")
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
