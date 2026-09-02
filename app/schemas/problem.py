"""题目相关请求模型（Step 1）。

字段与 api.md 一致：8 个必填字段 + 可选字段。
可选字段缺失时自动填充默认值（str → ""，list → []），
与「默认字段返回类型默认值」的要求对应。
"""
from pydantic import BaseModel


class Sample(BaseModel):
    input: str
    output: str


class TestCase(BaseModel):
    id: str | None = None   # 测试点编号（可选，缺省按数组顺序编号）
    input: str
    output: str


class ProblemConfig(BaseModel):
    # ---- 必填字段 ----
    id: str
    title: str
    description: str
    input_description: str
    output_description: str
    samples: list[Sample]
    constraints: str
    testcases: list[TestCase]

    # ---- 可选字段（带默认值）----
    hint: str = ""
    source: str = ""
    tags: list[str] = []
    time_limit: float = 3.0
    memory_limit: int = 128
    author: str = ""
    difficulty: str = ""
    public_cases: bool = False   # Step 5：测试点明细是否对普通用户可见


class LogVisibilityIn(BaseModel):
    public_cases: bool = False
