"""用户相关请求模型（Step 4）。"""
from pydantic import BaseModel, Field


class UsernamePassword(BaseModel):
    username: str
    password: str


class RegisterIn(UsernamePassword):
    pass


class LoginIn(UsernamePassword):
    pass


class RoleIn(BaseModel):
    role: str   # admin / user / banned


class UsernameIn(BaseModel):
    username: str = Field(min_length=3, max_length=40)
