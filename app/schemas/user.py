"""用户相关请求模型（Step 4）。"""
from pydantic import BaseModel, Field


class UsernamePassword(BaseModel):
    """登录和注册共用的用户名、密码字段。"""
    username: str
    password: str


class RegisterIn(UsernamePassword):
    """注册普通用户的请求体。"""
    pass


class LoginIn(UsernamePassword):
    """登录并创建会话的请求体。"""
    pass


class RoleIn(BaseModel):
    """管理员调整用户角色的请求体。"""
    role: str   # admin / user / banned


class UsernameIn(BaseModel):
    """用户修改本人用户名的请求体。"""
    username: str = Field(min_length=3, max_length=40)
