"""题目数字编号迁移：同步数据库内对旧文件编号的引用。"""
import re

from sqlalchemy import select, update

from app import config
from app.database import SessionLocal
from app.models import AccessLog, AiTask, Submission
from app.schemas.problem import PROBLEM_ID_RE


def _numeric(value: object) -> bool:
    return bool(re.fullmatch(PROBLEM_ID_RE, str(value)))


async def migrate_problem_references(mapping: dict[str, str]) -> dict[str, str]:
    """迁移全部历史引用，包括已删除题目留下的非数字编号。"""
    mapping = dict(mapping)
    async with SessionLocal() as db:
        submissions = (await db.scalars(select(Submission.problem_id).distinct())).all()
        access_logs = (await db.scalars(select(AccessLog.problem_id).distinct())).all()
        tasks = (await db.scalars(select(AiTask))).all()
        referenced = {str(value) for value in [*submissions, *access_logs] if value is not None}
        referenced.update(str(task.problem_id) for task in tasks if task.problem_id is not None)
        referenced.update(
            str(task.result["id"])
            for task in tasks
            if isinstance(task.result, dict) and task.result.get("id") is not None
        )

        used = {
            path.stem for path in config.PROBLEMS_DIR.glob("*.json") if _numeric(path.stem)
        }
        used.update(value for value in mapping.values() if _numeric(value))
        used.update(value for value in referenced if _numeric(value))
        next_id = max([1000, *(int(value) for value in used)]) + 1
        for old_id in sorted(value for value in referenced if not _numeric(value)):
            if old_id in mapping:
                continue
            while str(next_id) in used:
                next_id += 1
            mapping[old_id] = str(next_id)
            used.add(str(next_id))
            next_id += 1

        for old_id, new_id in mapping.items():
            await db.execute(
                update(Submission).where(Submission.problem_id == old_id).values(problem_id=new_id)
            )
            await db.execute(
                update(AccessLog).where(AccessLog.problem_id == old_id).values(problem_id=new_id)
            )
            await db.execute(
                update(AiTask).where(AiTask.problem_id == old_id).values(problem_id=new_id)
            )
        for task in tasks:
            if isinstance(task.result, dict) and str(task.result.get("id")) in mapping:
                task.result = {**task.result, "id": mapping[str(task.result["id"])]}
        await db.commit()
    return mapping
