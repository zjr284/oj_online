"""评测编排（Step 3）：提交后异步执行判题，更新提交记录与测试点明细。

- schedule_judge：用 asyncio.create_task 启动后台评测（不阻塞请求线程）；
- 代际（generation）机制：rejudge 取消旧任务并开启新任务时，旧任务即便
  在取消后仍完成，其结果也会被丢弃，避免覆盖新结果；
- requeue_pending：服务重启时重新评测遗留的 pending 提交。
"""
import asyncio
import logging
import shutil
import tempfile
from collections import Counter
from pathlib import Path

from sqlalchemy import delete, select

from app.core.errors import ApiError
from app.database import SessionLocal
from app.judge.runner import AC, CE, JudgeRunner
from app.models import Language, Submission, TestcaseResult
from app.services.problem_store import store

logger = logging.getLogger(__name__)

# 进行中的评测任务与其代际
_judge_tasks: dict[int, asyncio.Task] = {}
_generations: dict[int, int] = {}

POINTS_PER_CASE = 10   # Step 2：一个测试点 10 分


def schedule_judge(submission_id: int) -> None:
    """创建后台任务执行评测。"""
    _generations[submission_id] = _generations.get(submission_id, 0) + 1
    gen = _generations[submission_id]
    task = asyncio.create_task(judge_submission(submission_id, gen))
    _judge_tasks[submission_id] = task
    task.add_done_callback(lambda _t: _judge_tasks.pop(submission_id, None))


def cancel_judge(submission_id: int) -> None:
    """取消进行中的评测任务（rejudge 前调用）。"""
    task = _judge_tasks.get(submission_id)
    if task is not None and not task.done():
        task.cancel()


async def judge_submission(submission_id: int, generation: int) -> None:
    """评测入口：异常兜底，保证任何情况下提交都有终态。"""
    workdir = Path(tempfile.mkdtemp(prefix="oj-judge-"))
    try:
        await _judge(submission_id, generation, workdir)
    except asyncio.CancelledError:
        raise   # rejudge 取消：保持 pending，由新任务接手
    except ApiError as e:
        await _finish(submission_id, generation, "error", 0, {"UNK": 1}, None, [], error_info=e.msg)
    except Exception:
        logger.exception("judge failed for submission %s", submission_id)
        await _finish(submission_id, generation, "error", 0, {"UNK": 1}, None, [], error_info="judge internal error")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


async def _judge(submission_id: int, generation: int, workdir: Path) -> None:
    async with SessionLocal() as db:
        sub = await db.get(Submission, submission_id)
        if sub is None:
            return
        problem = await store.get(sub.problem_id)
        language = await db.get(Language, sub.language)

    if language is None:
        await _finish(submission_id, generation, "error", 0, {"UNK": 1}, None, [], error_info="language not found")
        return

    language_cfg = {
        "name": language.name,
        "file_ext": language.file_ext,
        "compile_cmd": language.compile_cmd,
        "run_cmd": language.run_cmd,
        "time_limit": language.time_limit,
        "memory_limit": language.memory_limit,
    }

    runner = JudgeRunner(language_cfg, problem, workdir)
    compiled, compile_info = await runner.compile(sub.code)
    if not compiled:
        # 编译失败：整体 CE（Step 2 结果集合）
        await _finish(submission_id, generation, "error", 0, {"CE": 1}, compile_info, [])
        return

    results = []
    for idx, case in enumerate(problem["testcases"]):
        results.append(await runner.run_case(case, idx))

    counts = dict(Counter(r.result for r in results))
    status = "success" if all(r.result == AC for r in results) else "error"
    score = counts.get(AC, 0) * POINTS_PER_CASE

    run_info = None
    if status == "error":
        first_bad = next((r for r in results if r.result != AC), None)
        if first_bad is not None:
            run_info = f"first failure at case {first_bad.case_id}: {first_bad.result}"
            if first_bad.detail:
                run_info += f"\n{first_bad.detail}"

    await _finish(submission_id, generation, status, score, counts, compile_info, results, run_info=run_info)


async def _finish(submission_id: int, generation: int, status: str, score: float, counts: dict,
                  compile_info: str | None, results: list, run_info: str | None = None,
                  error_info: str | None = None) -> None:
    """写回评测结果。若期间发生了 rejudge（代际变化），丢弃本次结果。"""
    if _generations.get(submission_id) != generation:
        return

    async with SessionLocal() as db:
        sub = await db.get(Submission, submission_id)
        if sub is None:
            return
        sub.status = status
        sub.score = score
        sub.counts = counts
        sub.compile_info = compile_info
        sub.run_info = run_info
        sub.error_info = error_info
        await db.execute(delete(TestcaseResult).where(TestcaseResult.submission_id == submission_id))
        for r in results:
            db.add(TestcaseResult(
                submission_id=submission_id, case_id=r.case_id,
                result=r.result, time=r.time, memory=r.memory,
            ))
        await db.commit()


async def requeue_pending() -> None:
    """服务启动时重新评测遗留的 pending 提交。"""
    async with SessionLocal() as db:
        ids = (await db.scalars(select(Submission.id).where(Submission.status == "pending"))).all()
    for sid in ids:
        schedule_judge(sid)
