"""评测提交请求模型（Step 3）。"""
from pydantic import BaseModel, Field
from app.schemas.problem import PROBLEM_ID_RE


class SubmissionIn(BaseModel):
    problem_id: str = Field(pattern=PROBLEM_ID_RE)
    language: str = Field(min_length=1)
    code: str = Field(min_length=1)
