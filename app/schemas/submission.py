"""评测提交请求模型（Step 3）。"""
from pydantic import BaseModel, Field


class SubmissionIn(BaseModel):
    problem_id: str = Field(min_length=1)
    language: str = Field(min_length=1)
    code: str = Field(min_length=1)
