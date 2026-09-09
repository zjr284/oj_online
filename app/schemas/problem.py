"""题目相关请求模型（Step 1）。

字段与 api.md 一致：8 个必填字段 + 可选字段。
可选字段缺失时自动填充默认值（str → ""，list → []），
与「默认字段返回类型默认值」的要求对应。

安全：id 支持文档示例中的 P1001、sum_2 等字符串标识，同时限制为
字母、数字、下划线和连字符，杜绝经请求体 id 的路径穿越；
time/memory 限制必须为正数。
"""
from pydantic import BaseModel, Field

# api.md 将题目 id 定义为 str，并使用 P1001、sum_2、max_num 等示例。
# 限制为安全文件名字符集；64 字符与数据库中的 problem_id 列一致。
PROBLEM_ID_RE = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"


class Sample(BaseModel):
    """题面公开展示的一组输入输出样例。"""
    input: str
    output: str


class TestCase(BaseModel):
    """评测使用的单个输入输出测试点。"""
    id: str | None = None   # 测试点编号（可选，缺省按数组顺序编号）
    input: str
    output: str


class ProblemConfig(BaseModel):
    """题目 JSON 的完整结构，也是新建和编辑接口的请求体。"""
    # ---- 必填字段 ----
    id: str = Field(pattern=PROBLEM_ID_RE, description="题目唯一字符串标识")
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
    """管理员修改测试点明细是否对普通用户公开的请求体。"""
    public_cases: bool = False
