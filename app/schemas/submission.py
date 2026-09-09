"""评测提交请求模型（Step 3）。"""
from pydantic import BaseModel, Field
from app.schemas.problem import PROBLEM_ID_RE


class SubmissionIn(BaseModel):
    """创建一次评测提交所需的题号、语言和源代码。"""
    problem_id: str = Field(pattern=PROBLEM_ID_RE)
    language: str = Field(min_length=1)
    code: str = Field(min_length=1)
