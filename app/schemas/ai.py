"""Advance：AI 智能命题请求模型。

接口规格见 api.md「AI 智能命题接口（建议路径）」：
PUT /api/ai/model-config 与 POST /api/ai/problem-tasks/。
"""
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator
from app.schemas.problem import PROBLEM_ID_RE


class ModelConfigIn(BaseModel):
    provider_url: str = Field(min_length=1, description="OpenAI 兼容的 chat/completions 接口地址")
    model: str = Field(min_length=1, description="模型名称")
    api_key: str = Field(min_length=1, description="模型密钥（加密存储，永不返回）")
    # 缺省表示未知价格，不能当作免费调用。
    input_price: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    output_price: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    price_unit: int | None = Field(default=None, ge=1, description="计价 Token 单位（如 1000000）")
    currency: str = Field(default="CNY", pattern=r"^[A-Z]{3}$")

    @field_validator("provider_url", "model", "api_key")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()

    @field_validator("provider_url")
    @classmethod
    def provider_address(cls, value):
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("provider_url must be an HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("put credentials only in api_key; URL must not contain credentials, query or fragment")
        return value


class AiTaskIn(BaseModel):
    requirement: str = Field(min_length=1, description="命题需求描述")
    problem_id: str | None = Field(default=None, pattern=PROBLEM_ID_RE)

    @field_validator("requirement")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("requirement must not be blank")
        return value.strip()
