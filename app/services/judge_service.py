"""评测编排（Step 3）：提交后异步执行判题，更新提交记录与测试点明细。

- schedule_judge：用 asyncio.create_task 启动后台评测（不阻塞请求线程）；
- 代际（generation）机制：rejudge 取消旧任务并开启新任务时，旧任务即便
  在取消后仍完成，其结果也会被丢弃，避免覆盖新结果；
- requeue_pending：服务重启时重新评测遗留的 pending 提交。
"""
import asyncio
import json
import logging
import shutil
import tempfile
from collections import Counter
from pathlib import Path

from sqlalchemy import delete, select

from app.core.errors import ApiError
from app.database import SessionLocal
from app.judge.runner import AC, UNK, JudgeRunner
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
    def done(completed):
        if _judge_tasks.get(submission_id) is completed:
            _judge_tasks.pop(submission_id, None)
    task.add_done_callback(done)


async def cancel_judge(submission_id: int) -> None:
    """取消进行中的评测任务（rejudge 前调用）。"""
    _generations[submission_id] = _generations.get(submission_id, 0) + 1
    task = _judge_tasks.get(submission_id)
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def shutdown() -> None:
    for sid in list(_judge_tasks):
        await cancel_judge(sid)
    _judge_tasks.clear()
    _generations.clear()


async def judge_submission(submission_id: int, generation: int) -> None:
    """评测入口：异常兜底，保证任何情况下提交都有终态。"""
    workdir = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="oj-judge-"))
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
        await asyncio.to_thread(shutil.rmtree, workdir, ignore_errors=True)


async def _judge(submission_id: int, generation: int, workdir: Path) -> None:
    async with SessionLocal() as db:
        sub = await db.get(Submission, submission_id)
        if sub is None:
            return
        problem = await store.get(sub.problem_id, for_judge=True)
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

    total = len(problem["testcases"]) * POINTS_PER_CASE   # api.md：counts = 本题总分数

    runner = JudgeRunner(language_cfg, problem, workdir)
    compiled, compile_info = await runner.compile(sub.code)
    if not compiled:
        # 编译失败：整体 CE（Step 2 结果集合）
        compile_obj = {"result": "failed", "message": compile_info or ""}
        await _finish(submission_id, generation, "error", 0, {"CE": 1}, compile_obj, [],
                      total_score=total)
        return

    results = []
    for idx, case in enumerate(problem["testcases"]):
        results.append(await runner.run_case(case, idx))

    counts = dict(Counter(r.result for r in results))
    # success 表示完成判题；WA/TLE/MLE/RE 是正常产生的测试点结果。
    status = "error" if any(r.result == UNK for r in results) else "success"
    score = counts.get(AC, 0) * POINTS_PER_CASE

    # api.md：run_info = {"result": ..., "message": ...}（运行阶段总体结果）
    run_msg = f"{len(results)} test cases finished"
    if any(r.result != AC for r in results):
        first_bad = next((r for r in results if r.result != AC), None)
        if first_bad is not None:
            run_msg += f"; first failure at case {first_bad.case_id}: {first_bad.result}"
            if first_bad.detail:
                run_msg += f"\n{first_bad.detail}"
    run_info = {"result": "finished", "message": run_msg}

    compile_obj = ({"result": "success", "message": compile_info or ""}
                   if language.compile_cmd else None)
    await _finish(submission_id, generation, status, score, counts, compile_obj, results,
                  run_info=run_info, total_score=total,
                  error_info="cannot start judge process" if status == "error" else None)


async def _finish(submission_id: int, generation: int, status: str, score: float, counts: dict,
                  compile_info: dict | None, results: list, run_info: dict | None = None,
                  error_info: str | None = None, total_score: int | None = None) -> None:
    """写回评测结果。若期间发生了 rejudge（代际变化），丢弃本次结果。

    compile_info / run_info 以 JSON 字符串落库，响应时解析为 api.md 的对象结构
    （{"result": ..., "message": ...}）；解释型语言 compile_info 为 None。
    """
    if _generations.get(submission_id) != generation:
        return

    async with SessionLocal() as db:
        sub = await db.get(Submission, submission_id)
        if sub is None:
            return
        if _generations.get(submission_id) != generation:
            return
        sub.status = status
        sub.score = score
        sub.total_score = total_score
        sub.counts = counts
        sub.compile_info = json.dumps(compile_info, ensure_ascii=False) if compile_info else None
        sub.run_info = json.dumps(run_info, ensure_ascii=False) if run_info else None
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


async def normalize_legacy_results() -> None:
    """保留历史得分和日志，修复旧版把 WA/TLE/MLE/RE 标为 error 的记录。"""
    async with SessionLocal() as db:
        records = (await db.scalars(select(Submission).where(Submission.status != "pending"))).all()
        for sub in records:
            if sub.counts and set(sub.counts) <= {"AC", "WA", "TLE", "MLE", "RE"}:
                sub.status = "success"
                language = await db.get(Language, sub.language)
                if language and language.compile_cmd:
                    raw = sub.compile_info
                    try:
                        value = json.loads(raw) if raw else None
                    except ValueError:
                        value = raw
                    if not isinstance(value, dict):
                        sub.compile_info = json.dumps({"result": "success", "message": value or ""})
        await db.commit()
