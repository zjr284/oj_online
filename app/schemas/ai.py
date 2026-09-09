"""Advance：AI 智能命题请求模型。

接口规格见 api.md「AI 智能命题接口（建议路径）」：
PUT /api/ai/model-config 与 POST /api/ai/problem-tasks/。
"""
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator
from app.schemas.problem import PROBLEM_ID_RE


class ModelConfigIn(BaseModel):
    """用户提交的模型连接、密钥和可选计价配置。"""
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
        """拒绝只由空白组成的连接地址、模型名称或密钥。"""
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()

    @field_validator("provider_url")
    @classmethod
    def provider_address(cls, value):
        """限制模型地址为不含嵌入式凭据的 HTTP(S) URL。"""
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("provider_url must be an HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("put credentials only in api_key; URL must not contain credentials, query or fragment")
        return value

    @model_validator(mode="after")
    def complete_manual_price_pair(self):
        """手工计价必须同时提供输入/输出单价，避免静默显示未知。"""
        if (self.input_price is None) != (self.output_price is None):
            raise ValueError("input_price and output_price must be provided together")
        return self


class AiTaskIn(BaseModel):
    """创建 AI 命题任务的需求、参考题目和生成档位。"""
    requirement: str = Field(min_length=1, description="命题需求描述")
    problem_id: str | None = Field(default=None, pattern=PROBLEM_ID_RE)
    generation_mode: Literal["fast", "balanced", "quality"] | None = Field(
        default=None,
        description="DeepSeek 命题模式；不传时沿用配置中的原始模型行为",
    )

    @field_validator("requirement")
    @classmethod
    def nonblank(cls, value):
        """拒绝空白命题需求，保证模型能获得有效输入。"""
        if not value.strip():
            raise ValueError("requirement must not be blank")
        return value.strip()


class AiRefineIn(BaseModel):
    """基于已完成题目继续修改时提交的意见与档位。"""
    requirement: str = Field(min_length=1, description="对上一版题目的修改要求")
    generation_mode: Literal["fast", "balanced", "quality"] | None = Field(
        default=None,
        description="本轮使用的 DeepSeek 命题模式；不传时沿用上一版",
    )

    @field_validator("requirement")
    @classmethod
    def nonblank(cls, value):
        """拒绝空白修改意见，避免无意义地创建新版本。"""
        if not value.strip():
            raise ValueError("requirement must not be blank")
        return value.strip()
