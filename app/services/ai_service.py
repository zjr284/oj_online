"""Advance：AI 智能命题服务。

设计要点（api.md 安全要求 + advance.md 评分点）：
- 模型配置：provider_url / model / 价格明文存 data/ai_config.json，
  api_key 经 Fernet 加密后同文件存储（密钥文件 data/ai_secret.key，600 权限）；
  api_key 永不通过接口、日志或错误信息返回（报错文本做脱敏）。
- 任务编排：asyncio.create_task 后台执行；cancel 直接 task.cancel() 真正终止，
  并以 _cancelled 集合 + DB 状态双重保护，避免后台任务把已取消的任务改回 done。
- 实时进度：进度事件推入内存队列，经 SSE 接口下发；同时进度数值落库，
  支持轮询兜底（api.md 允许 SSE/流式/轮询）。
- 调用协议：OpenAI 兼容的 chat/completions（provider_url 由用户配置，不写死厂商）；
  生成结果经 ProblemConfig 校验后才写入任务（安全要求：校验模型返回数据）。
- 与基础功能解耦：本模块只「生成+校验」，不直接写题库——
  前端经已有的 POST/PUT /api/problems/ 接口导入，避免与题目管理冲突。
- 计费公式：输入Token/price_unit × input_price + 输出Token/price_unit × output_price；
  模型接口不返回用量时按 字符数/4 估算，usage.estimated=true 并在页面标注。
"""
import asyncio
import json
import re
from datetime import datetime

import httpx
from cryptography.fernet import Fernet
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.core.errors import ApiError
from app.database import SessionLocal
from app.models import AiTask, User
from app.schemas.ai import AiTaskIn, ModelConfigIn
from app.schemas.problem import ProblemConfig
from app.services.problem_store import store

# ---- 常量 ----
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_CANCELLED = "cancelled"
STATUS_FAILED = "failed"
TERMINAL_STATUSES = (STATUS_DONE, STATUS_CANCELLED, STATUS_FAILED)

DEFAULT_PRICE_UNIT = 1_000_000      # api.md 示例计价单位（每百万 Token）
CURRENCY = "CNY"
REQUEST_TIMEOUT = 120.0             # 模型调用总超时（秒），失败即任务 failed

CONFIG_PATH = config.DATA_DIR / "ai_config.json"
KEY_PATH = config.DATA_DIR / "ai_secret.key"

# 运行中的后台任务与事件队列（进程内；重启后由 fail_stale_tasks 兜底）
_tasks: dict[int, asyncio.Task] = {}
_events: dict[int, asyncio.Queue] = {}
_cancelled: set[int] = set()

SYSTEM_PROMPT = """你是 OJ 在线评测系统的出题助手。请根据用户需求生成一道编程题，并只输出一个 JSON 对象（不要 markdown 代码块，不要任何多余文字），字段如下：
{
  "id": "简短唯一的英文标识（如 sum_of_two）",
  "title": "题目名称",
  "description": "题目描述",
  "input_description": "输入格式说明",
  "output_description": "输出格式说明",
  "samples": [{"input": "...", "output": "..."}],
  "constraints": "数据范围与限制",
  "testcases": [{"id": "1", "input": "...", "output": "..."}],
  "hint": "提示",
  "source": "来源",
  "tags": ["标签"],
  "time_limit": 1.0,
  "memory_limit": 128,
  "author": "AI",
  "difficulty": "入门/简单/中等/困难"
}
要求：samples 给出 1~2 个样例；testcases 给出 4~8 个测试点，覆盖最小/最大边界与特殊情形；time_limit、memory_limit 取值合理；难度与知识点符合需求。"""


# ---- 模型配置（api_key 加密存储） ----

class ModelConfigStore:
    """模型配置存取：api_key 经 Fernet 加密，只读时在内存中解密。"""

    async def load(self) -> dict | None:
        """读取完整配置（含解密后的 api_key）；未配置返回 None。"""
        def _read() -> dict | None:
            if not CONFIG_PATH.is_file():
                return None
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            try:
                data["api_key"] = Fernet(self._load_key()).decrypt(
                    data.pop("api_key_encrypted").encode()
                ).decode()
            except Exception:
                raise ApiError(500, "ai config corrupted: cannot decrypt api_key")
            return data

        return await asyncio.to_thread(_read)

    async def save(self, cfg: ModelConfigIn) -> dict:
        """保存配置；返回不含 api_key 的公开字段（api.md）。"""
        def _write() -> dict:
            key = self._load_key()
            data = {
                "provider_url": cfg.provider_url.strip(),
                "model": cfg.model.strip(),
                "input_price": cfg.input_price if cfg.input_price is not None else 0.0,
                "output_price": cfg.output_price if cfg.output_price is not None else 0.0,
                "price_unit": cfg.price_unit or DEFAULT_PRICE_UNIT,
                "api_key_encrypted": Fernet(key).encrypt(cfg.api_key.strip().encode()).decode(),
            }
            CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            public = {k: v for k, v in data.items() if k != "api_key_encrypted"}
            public["api_key_configured"] = True
            return public

        return await asyncio.to_thread(_write)

    @staticmethod
    def _load_key() -> bytes:
        """读取或生成 Fernet 密钥文件（600 权限）。"""
        if KEY_PATH.is_file():
            return KEY_PATH.read_bytes()
        key = Fernet.generate_key()
        KEY_PATH.write_bytes(key)
        KEY_PATH.chmod(0o600)
        return key


config_store = ModelConfigStore()


# ---- 提示词与结果解析 ----

def build_prompt(requirement: str, reference: dict | None) -> str:
    parts = [f"命题需求：{requirement}"]
    if reference:
        parts.append(
            "以下是一道已有题目，请在保持其 id 不变的基础上按要求修改/改编：\n"
            + json.dumps(reference, ensure_ascii=False, indent=2)
        )
    return "\n\n".join(parts)


def parse_problem(content: str) -> dict:
    """解析模型输出为题目配置；必须通过 ProblemConfig 校验（api.md）。"""
    text = content.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 兜底：截取第一个 { 到最后一个 }
        s, e = text.find("{"), text.rfind("}")
        if s == -1 or e <= s:
            raise RuntimeError("model output is not valid JSON")
        try:
            data = json.loads(text[s:e + 1])
        except json.JSONDecodeError:
            raise RuntimeError("model output is not valid JSON")
    try:
        return ProblemConfig.model_validate(data).model_dump()
    except ValidationError as e:
        raise RuntimeError(f"generated problem failed validation: {str(e)[:300]}")


def build_usage(cfg: dict, raw: dict, content: str, prompt: str) -> dict:
    """Token 用量与费用统计（api.md 费用公式）。

    模型接口不提供用量时按 字符数/4 估算，estimated=true 由前端/文档标注。
    """
    unit = cfg["price_unit"] or DEFAULT_PRICE_UNIT
    inp, out = raw.get("prompt_tokens"), raw.get("completion_tokens")
    estimated = inp is None or out is None
    if inp is None:
        inp = max(1, len(prompt) // 4)
    if out is None:
        out = max(1, len(content) // 4)
    inp, out = int(inp), int(out)
    cost = round(inp / unit * cfg["input_price"] + out / unit * cfg["output_price"], 6)
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "total_tokens": inp + out,
        "cost": cost,
        "currency": CURRENCY,
        "price_unit": unit,
        "input_price": cfg["input_price"],
        "output_price": cfg["output_price"],
        "estimated": estimated,
    }


def _sanitize(msg: str, secret: str | None) -> str:
    """错误信息脱敏：绝不泄露 api_key（api.md）。"""
    if secret and secret in msg:
        msg = msg.replace(secret, "***")
    return msg[:500]


async def _request_model(cfg: dict, payload: dict) -> dict:
    """向 OpenAI 兼容接口发起请求并解析响应（独立函数便于测试 mock）。"""
    headers = {"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"}
    timeout = httpx.Timeout(REQUEST_TIMEOUT, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(cfg["provider_url"], json=payload, headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(f"model api returned HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json()


async def _call_model(cfg: dict, prompt: str) -> tuple[dict, str]:
    """调用 OpenAI 兼容 chat/completions 协议；返回 (usage, content)。"""
    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 4096,
    }
    data = await _request_model(cfg, payload)
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("unexpected model response format")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("model returned empty content")
    return build_usage(cfg, data.get("usage") or {}, content, prompt), content


# ---- 任务编排 ----

def _serialize(t: AiTask) -> dict:
    return {
        "task_id": t.id,
        "status": t.status,
        "progress": t.progress,
        "result": t.result,
        "usage": t.usage,
        "requirement": t.requirement,
        "problem_id": t.problem_id,
        "provider_url": t.provider_url,
        "model": t.model,
        "created_at": t.created_at.strftime("%Y-%m-%d %H:%M:%S") if t.created_at else None,
    }


async def _push(task_id: int, event: str, data: dict) -> None:
    """向 SSE 事件队列推送（无订阅者时静默丢弃）。"""
    q = _events.get(task_id)
    if q is not None:
        await q.put((event, data))


async def _progress(task_id: int, progress: float, message: str) -> None:
    """更新任务进度：落库（轮询可见）+ 推送 SSE 事件。"""
    async with SessionLocal() as db:
        t = await db.get(AiTask, task_id)
        if t is not None and task_id not in _cancelled:
            t.status = STATUS_RUNNING
            t.progress = progress
            await db.commit()
    await _push(task_id, "progress", {
        "task_id": task_id, "status": STATUS_RUNNING, "progress": progress, "message": message,
    })


async def _run_task(task_id: int) -> None:
    """后台执行命题任务：调用模型 → 校验 → 落库。所有异常在此兜底。"""
    cfg = None
    try:
        async with SessionLocal() as db:
            t = await db.get(AiTask, task_id)
            if t is None or task_id in _cancelled:
                return
            requirement, problem_id = t.requirement, t.problem_id
        await _progress(task_id, 0.05, "任务开始")

        cfg = await config_store.load()
        if cfg is None:
            raise RuntimeError("model config not set")

        reference = await store.get(problem_id) if problem_id else None
        prompt = build_prompt(requirement, reference)

        await _progress(task_id, 0.15, "正在调用模型…")
        usage, content = await _call_model(cfg, prompt)

        await _progress(task_id, 0.7, "模型已返回，正在解析校验…")
        problem = parse_problem(content)

        await _progress(task_id, 0.9, "校验通过，正在保存结果…")
        if task_id in _cancelled:
            return
        async with SessionLocal() as db:
            t = await db.get(AiTask, task_id)
            t.status = STATUS_DONE
            t.progress = 1.0
            t.result = problem
            t.usage = usage
            await db.commit()
        await _push(task_id, "final", {
            "task_id": task_id, "status": STATUS_DONE, "progress": 1.0,
            "result": problem, "usage": usage, "message": "完成",
        })
    except asyncio.CancelledError:
        # 被 cancel 接口真正终止：不落库（状态已由 cancel 置 cancelled）
        raise
    except Exception as e:
        secret = cfg.get("api_key") if cfg else None
        msg = _sanitize(str(e), secret)
        if task_id not in _cancelled:
            try:
                async with SessionLocal() as db:
                    t = await db.get(AiTask, task_id)
                    if t is not None:
                        t.status = STATUS_FAILED
                        t.result = {"error": msg}
                        await db.commit()
            except Exception:
                pass
        await _push(task_id, "final", {
            "task_id": task_id, "status": STATUS_FAILED, "message": msg,
        })
    finally:
        _tasks.pop(task_id, None)


async def create_task(user: User, body: AiTaskIn) -> AiTask:
    """创建命题任务并启动后台执行；无模型配置 400，参考题目不存在 404。"""
    cfg = await config_store.load()
    if cfg is None:
        raise ApiError(400, "model config not set")
    if body.problem_id:
        try:
            await store.get(body.problem_id)
        except ApiError as e:
            if e.status == 404:
                raise ApiError(404, "problem not found")
            raise
    async with SessionLocal() as db:
        t = AiTask(
            user_id=user.id, requirement=body.requirement, problem_id=body.problem_id,
            status=STATUS_PENDING, progress=0.0,
            provider_url=cfg["provider_url"], model=cfg["model"],
        )
        db.add(t)
        await db.commit()
        await db.refresh(t)
        task_id = t.id
    _cancelled.discard(task_id)
    _tasks[task_id] = asyncio.create_task(_run_task(task_id))
    return t


async def list_tasks(user: User, limit: int = 50) -> list[dict]:
    """任务列表（等价扩展，便于 R1 交互）：本人可见自己的任务，管理员可见全部。

    列表项不含 result（体积较大），详情经 GET /problem-tasks/{task_id} 获取。
    """
    async with SessionLocal() as db:
        stmt = select(AiTask).order_by(AiTask.id.desc()).limit(limit)
        if user.role != "admin":
            stmt = stmt.where(AiTask.user_id == user.id)
        tasks = (await db.scalars(stmt)).all()
        items = []
        for t in tasks:
            d = _serialize(t)
            d.pop("result", None)
            items.append(d)
        return items


async def get_task(user: User, task_id: int) -> dict:
    """任务状态查询：创建者或管理员。"""
    async with SessionLocal() as db:
        t = await db.get(AiTask, task_id)
        if t is None:
            raise ApiError(404, "task not found")
        if user.role != "admin" and t.user_id != user.id:
            raise ApiError(403, "permission denied")
        return _serialize(t)


async def cancel_task(user: User, task_id: int) -> str:
    """取消任务：真正终止后台执行；已结束 409（api.md）。"""
    async with SessionLocal() as db:
        t = await db.get(AiTask, task_id)
        if t is None:
            raise ApiError(404, "task not found")
        if user.role != "admin" and t.user_id != user.id:
            raise ApiError(403, "permission denied")
        if t.status in TERMINAL_STATUSES:
            raise ApiError(409, "task already finished")
        t.status = STATUS_CANCELLED
        await db.commit()
    _cancelled.add(task_id)
    task = _tasks.get(task_id)
    if task is not None:
        task.cancel()
    return STATUS_CANCELLED


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def sse_stream(state: dict, task_id: int):
    """SSE 进度事件流（权限校验已在路由层完成，避免响应开始后才抛异常）。

    先发当前状态，再推送 progress/final 事件；15s 心跳保活。
    """
    q = _events.setdefault(task_id, asyncio.Queue())
    try:
        yield _sse("state", state)
        if state["status"] in TERMINAL_STATUSES:
            return
        while True:
            try:
                event, data = await asyncio.wait_for(q.get(), timeout=15)
                yield _sse(event, data)
                if event == "final":
                    return
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"   # SSE 心跳注释（浏览器忽略）
    except Exception as e:
        yield _sse("final", {
            "task_id": task_id, "status": STATUS_FAILED, "message": _sanitize(str(e), None),
        })


async def fail_stale_tasks() -> None:
    """启动时清理：上次进程遗留的 pending/running 任务标记为 failed（api.md 失败兜底）。"""
    async with SessionLocal() as db:
        stale = (
            await db.scalars(
                select(AiTask).where(AiTask.status.in_([STATUS_PENDING, STATUS_RUNNING]))
            )
        ).all()
        for t in stale:
            t.status = STATUS_FAILED
            t.result = {"error": "interrupted by server restart"}
        if stale:
            await db.commit()
    _tasks.clear()
