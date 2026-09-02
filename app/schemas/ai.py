"""Advance：AI 智能命题请求模型。

接口规格见 api.md「AI 智能命题接口（建议路径）」：
PUT /api/ai/model-config 与 POST /api/ai/problem-tasks/。
"""
from pydantic import BaseModel, Field


class ModelConfigIn(BaseModel):
    provider_url: str = Field(min_length=1, description="OpenAI 兼容的 chat/completions 接口地址")
    model: str = Field(min_length=1, description="模型名称")
    api_key: str = Field(min_length=1, description="模型密钥（加密存储，永不返回）")
    # 计价：每 price_unit 个 Token 的价格；缺省时费用按 0 计算
    input_price: float | None = None
    output_price: float | None = None
    price_unit: int | None = Field(default=None, ge=1, description="计价 Token 单位（如 1000000）")


class AiTaskIn(BaseModel):
    requirement: str = Field(min_length=1, description="命题需求描述")
    problem_id: str | None = None   # 可选：参考/改编的已有题目
