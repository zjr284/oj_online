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
import math
import os
import re
import threading
from urllib.parse import urlsplit

import httpx
from cryptography.fernet import Fernet
from pydantic import ValidationError
from sqlalchemy import select, update
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
REQUEST_TIMEOUT = config.AI_REQUEST_TIMEOUT_SECONDS  # 模型调用总超时（默认 10 分钟）

GENERATION_PROFILES = {
    "fast": {
        "model": "deepseek-v4-flash",
        "thinking": "disabled",
        "reasoning_effort": None,
    },
    "balanced": {
        "model": "deepseek-v4-flash",
        "thinking": "enabled",
        "reasoning_effort": "low",
    },
    "quality": {
        "model": "deepseek-v4-pro",
        "thinking": "enabled",
        "reasoning_effort": "high",
    },
}

CONFIG_PATH = config.DATA_DIR / "ai_config.json"
KEY_PATH = config.DATA_DIR / "ai_secret.key"

# 运行中的后台任务与事件队列（进程内；重启后由 fail_stale_tasks 兜底）
_tasks: dict[int, asyncio.Task] = {}
_events: dict[int, set[asyncio.Queue]] = {}
_cancelled: set[int] = set()

SYSTEM_PROMPT = """你是 OJ 在线评测系统的出题助手。请根据用户需求生成一道编程题，并只输出一个 JSON 对象（不要 markdown 代码块，不要任何多余文字），字段如下：
{
  "id": "1–18 位唯一数字编号（如 2001）",
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
要求：明确落实用户指定的知识点、难度和每项约束；题面、输入输出、数据范围必须一致。
samples 给出至少两个可手工核验的样例。testcases 覆盖最小规模、零值/负数（适用时）、重复元素、
极值、退化结构和具有区分度的数据规模；按目标算法复杂度设计能识别常见错误和低效算法的用例。
不要只用几个很小的随机数字代替边界或复杂度测试。逐个复核期望输出与输入的对应关系。
time_limit、memory_limit 应与目标算法及数据范围相符。仅在用户需求适用时采用相应边界。"""


# ---- 模型配置（api_key 加密存储） ----

class ModelConfigStore:
    """模型配置存取：api_key 经 Fernet 加密，只读时在内存中解密。"""

    def __init__(self):
        self._lock = threading.RLock()

    async def _locked(self, operation):
        def run():
            with self._lock:
                return operation()
        return await asyncio.to_thread(run)

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

        return await self._locked(_read)

    async def save(self, cfg: ModelConfigIn) -> dict:
        """保存配置；返回不含 api_key 的公开字段（api.md）。"""
        def _write() -> dict:
            key = self._load_key()
            data = {
                "provider_url": cfg.provider_url.strip(),
                "model": cfg.model.strip(),
                # 价格为可选：None 表示未手动配置，费用按 provider 返回的费用自动计算
                "input_price": cfg.input_price,
                "output_price": cfg.output_price,
                "price_unit": cfg.price_unit or DEFAULT_PRICE_UNIT,
                "currency": cfg.currency,
                "api_key_encrypted": Fernet(key).encrypt(cfg.api_key.strip().encode()).decode(),
            }
            store._replace(CONFIG_PATH, json.dumps(data, ensure_ascii=False, indent=2))
            CONFIG_PATH.chmod(0o600)
            public = {k: v for k, v in data.items() if k != "api_key_encrypted"}
            public["api_key_configured"] = True
            return public

        return await self._locked(_write)

    @staticmethod
    def _load_key() -> bytes:
        """读取或生成 Fernet 密钥文件（600 权限）。"""
        if KEY_PATH.is_file():
            return KEY_PATH.read_bytes()
        key = Fernet.generate_key()
        KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(os.open(KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:
            f.write(key)
        return key


config_store = ModelConfigStore()


def supports_generation_modes(cfg: dict) -> bool:
    """三档预设只向 DeepSeek 官方接口发送其专有 thinking 参数。"""
    return (urlsplit(cfg.get("provider_url", "")).hostname or "").lower() == "api.deepseek.com"


def apply_generation_mode(cfg: dict, mode: str | None) -> dict:
    """返回本次任务的配置快照；不修改磁盘上的模型配置。"""
    effective = dict(cfg)
    if mode is None:
        return effective
    if mode not in GENERATION_PROFILES:
        raise ApiError(400, "invalid generation mode")
    if not supports_generation_modes(cfg):
        raise ApiError(400, "generation modes require the official DeepSeek API endpoint")

    profile = GENERATION_PROFILES[mode]
    configured_model = effective["model"]
    effective["model"] = profile["model"]
    effective["_generation_mode"] = mode
    effective["_thinking"] = profile["thinking"]
    effective["_reasoning_effort"] = profile["reasoning_effort"]

    # 手工价格属于配置时填写的模型。预设切换到另一模型时不能沿用，
    # 否则费用会被错误计算；若提供商直接返回 cost，仍优先使用它。
    if effective["model"] != configured_model:
        effective["input_price"] = None
        effective["output_price"] = None
    return effective


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
        result = ProblemConfig.model_validate(data).model_dump(exclude_none=True)
        # 日志公开权限只由管理员维护；模型不能修改它。
        result.pop("public_cases", None)
        return result
    except ValidationError as e:
        fields = [".".join(map(str, error["loc"])) for error in e.errors()]
        raise RuntimeError(f"generated problem failed validation: {', '.join(fields)[:200]}")


def build_usage(cfg: dict, raw: dict, content: str, prompt: str) -> dict:
    """Token 用量与费用统计（api.md 费用公式 + advance.md 计价依据透明）。

    费用来源优先级：
    1. provider：模型接口在 usage 中直接返回费用（usage.cost）；
    2. config：模型配置中填写的 input_price/output_price；
    3. unknown：未填价格且接口未返回费用，cost 为 None（页面明确标注）。
    用量缺失时按 字符数/4 估算，estimated=true 标注。
    """
    raw = raw if isinstance(raw, dict) else {}
    inp = raw.get("prompt_tokens", raw.get("input_tokens"))
    out = raw.get("completion_tokens", raw.get("output_tokens"))
    def valid_tokens(value):
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0
    inp = inp if valid_tokens(inp) else None
    out = out if valid_tokens(out) else None
    estimated = inp is None or out is None
    if inp is None:
        inp = max(1, math.ceil((len(SYSTEM_PROMPT) + len(prompt)) / 4))
    if out is None:
        out = max(1, math.ceil(len(content) / 4))
    inp, out = int(inp), int(out)

    base = {
        "input_tokens": inp,
        "output_tokens": out,
        "total_tokens": inp + out,
        "estimated": estimated,
    }

    # 1. 提供方直接计费
    if (isinstance(raw.get("cost"), (int, float)) and not isinstance(raw["cost"], bool)
            and math.isfinite(raw["cost"]) and raw["cost"] >= 0):
        return {
            **base,
            "cost": round(float(raw["cost"]), 6),
            "currency": raw.get("currency") or "USD",
            "price_source": "provider",
        }

    # 2. 用户手动配置价格
    if cfg.get("input_price") is not None and cfg.get("output_price") is not None:
        unit = cfg["price_unit"] or DEFAULT_PRICE_UNIT
        return {
            **base,
            "cost": round(inp / unit * cfg["input_price"] + out / unit * cfg["output_price"], 6),
            "currency": cfg.get("currency", CURRENCY),
            "price_unit": unit,
            "input_price": cfg["input_price"],
            "output_price": cfg["output_price"],
            "price_source": "config",
        }

    # 3. 无价格信息
    return {**base, "cost": None, "currency": cfg.get("currency", CURRENCY), "price_source": "unknown"}


def merge_usage(previous: dict | None, current: dict) -> dict:
    """重试也会计费；保留每次调用明细，汇总已知的全部调用。"""
    calls = [*(previous.get("calls", []) if previous else []), current]
    total = dict(current)
    total["calls"] = calls
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        total[key] = sum(call[key] for call in calls)
    total["estimated"] = any(call["estimated"] for call in calls)
    total["cost"] = (round(sum(call["cost"] for call in calls), 6)
                     if all(call["cost"] is not None and call["currency"] == current["currency"]
                            for call in calls) else None)
    if total["cost"] is None:
        total["price_source"] = "unknown"
    elif len({call["price_source"] for call in calls}) > 1:
        total["price_source"] = "mixed"
    return total


def _sanitize(msg: str, secret: str | None) -> str:
    """错误信息脱敏：绝不泄露 api_key（api.md）。"""
    if secret and secret in msg:
        msg = msg.replace(secret, "***")
    return msg[:500]


async def _request_model(cfg: dict, payload: dict) -> dict:
    """向 OpenAI 兼容接口发起请求并解析响应（独立函数便于测试 mock）。"""
    headers = {"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"}
    # 连接/写入超时用于快速识别错误地址；推理阶段的总时限由 _run_task
    # 外层唯一的 asyncio.wait_for 控制，避免 httpx 读超时与外层超时重复竞争。
    timeout = httpx.Timeout(None, connect=30.0, write=60.0, pool=30.0)
    url = cfg["provider_url"].rstrip("/")
    if not urlsplit(url).path or url.endswith("/v1"):
        url += "/chat/completions"
    host = urlsplit(url).hostname or "未知主机"
    try:
        # 默认忽略宿主机上遗留的 HTTP_PROXY/HTTPS_PROXY；如部署
        # 确实依赖系统代理，可通过 OJ_AI_TRUST_ENV=1 显式开启。
        async with httpx.AsyncClient(timeout=timeout, trust_env=config.AI_TRUST_ENV) as client:
            resp = await client.post(url, json=payload, headers=headers)
    except (httpx.ConnectError, httpx.ProxyError) as exc:
        proxy_hint = (
            "当前已启用系统代理，请确认代理服务可用。"
            if config.AI_TRUST_ENV else
            "当前默认直连；如必须使用系统代理，请设置 OJ_AI_TRUST_ENV=1 后重启。"
        )
        raise RuntimeError(
            f"无法连接模型服务 {host}；请检查 provider_url、DNS、"
            f"服务器出站网络和防火墙。{proxy_hint}"
        ) from exc
    except httpx.ConnectTimeout as exc:
        raise RuntimeError(
            f"连接模型服务 {host} 超时；请检查 DNS、出站网络和代理设置。"
        ) from exc
    except httpx.RequestError as exc:
        raise RuntimeError(
            f"模型服务 {host} 网络请求失败（{type(exc).__name__}）；"
            "请检查服务器网络后重试。"
        ) from exc
    if resp.status_code != 200:
        hint = ""
        if resp.status_code == 404:
            hint = ("（provider_url 需指向完整的 OpenAI 兼容 chat/completions 接口地址，"
                    "如 https://api.deepseek.com/chat/completions，不能只填域名）")
        safe_text = _sanitize(resp.text, cfg["api_key"])[:200]
        raise RuntimeError(f"model api returned HTTP {resp.status_code}: {safe_text}{hint}")
    return resp.json()


async def _call_model(cfg: dict, prompt: str, on_usage=None) -> tuple[dict, str]:
    """调用 OpenAI 兼容 chat/completions 协议；返回 (usage, content)。

    健壮性（api.md 要求处理模型调用失败）：
    - 不设置 max_tokens，由模型服务使用其自身允许的输出上限；
    - 推理模型（模型名含 reasoner）不传 temperature（DeepSeek R1 不支持该参数）；
    - 空输出自动重试一次（模型偶发）；仍为空时错误信息带 finish_reason 与排查提示。
    """
    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    }
    thinking = cfg.get("_thinking")
    if thinking:
        payload["thinking"] = {"type": thinking}
    if cfg.get("_reasoning_effort"):
        payload["reasoning_effort"] = cfg["_reasoning_effort"]
    if thinking != "enabled" and "reasoner" not in cfg["model"].lower():
        payload["temperature"] = 0.3

    usage = None
    for attempt in (1, 2):
        data = await _request_model(cfg, payload)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise RuntimeError("unexpected model response format")
        usage = merge_usage(usage, build_usage(cfg, data.get("usage"),
                                              content if isinstance(content, str) else "", prompt))
        if on_usage:
            await on_usage(usage)
        reason = (data.get("choices") or [{}])[0].get("finish_reason")
        if reason == "length":
            raise RuntimeError(
                "model output stopped (finish_reason=length)"
                "（OJ 未设置 max_tokens；模型服务已达到自身的输出或上下文上限，"
                "请缩短命题要求或选择上下文上限更大的模型）"
            )
        if isinstance(content, str) and content.strip():
            return usage, content
        if attempt == 1:
            continue   # 空输出：重试一次
        hint = "（请确认模型名为服务商提供的有效 API ID）"
        raise RuntimeError(f"model returned empty content (finish_reason={reason}){hint}")


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
        "generation_mode": t.generation_mode,
        "created_at": t.created_at.strftime("%Y-%m-%d %H:%M:%S") if t.created_at else None,
    }


async def _push(task_id: int, event: str, data: dict) -> None:
    """向 SSE 事件队列推送（无订阅者时静默丢弃）。"""
    for q in tuple(_events.get(task_id, ())):
        if q.full():
            q.get_nowait()
        q.put_nowait((event, data))


async def _progress(task_id: int, progress: float, message: str) -> None:
    """更新任务进度：落库（轮询可见）+ 推送 SSE 事件。"""
    if task_id in _cancelled:
        return   # 已取消：不再落库/推送 running 事件，避免界面闪回
    async with SessionLocal() as db:
        t = await db.get(AiTask, task_id)
        if t is not None and t.status not in TERMINAL_STATUSES and task_id not in _cancelled:
            t.status = STATUS_RUNNING
            t.progress = progress
            await db.commit()
    await _push(task_id, "progress", {
        "task_id": task_id, "status": STATUS_RUNNING, "progress": progress, "message": message,
    })


async def _run_task(task_id: int, cfg: dict | None = None) -> None:
    """后台执行命题任务：调用模型 → 校验 → 落库。所有异常在此兜底。"""
    try:
        async with SessionLocal() as db:
            t = await db.get(AiTask, task_id)
            if t is None or task_id in _cancelled:
                return
            requirement, problem_id = t.requirement, t.problem_id
        await _progress(task_id, 0.05, "任务开始")

        cfg = cfg or await config_store.load()
        if cfg is None:
            raise RuntimeError("model config not set")

        reference = await store.get(problem_id) if problem_id else None
        prompt = build_prompt(requirement, reference)

        await _progress(task_id, 0.15, "正在调用模型…")
        # 模型调用期间每 2s 推送一次进度（advance.md R3：
        # 「执行期间界面应持续展示可观察的进度信息」，而非静默等待结果）
        async def _ticker():
            elapsed = 0.0
            while task_id not in _cancelled:
                await asyncio.sleep(2)
                elapsed += 2
                p = min(0.15 + elapsed / max(REQUEST_TIMEOUT, 1) * 0.5, 0.65)
                await _progress(task_id, p, f"模型推理中（已 {int(elapsed)}s）…")

        ticker = asyncio.create_task(_ticker())
        try:
            async def record_usage(usage):
                async with SessionLocal() as db:
                    await db.execute(update(AiTask).where(AiTask.id == task_id)
                                     .values(usage=usage))
                    await db.commit()
                await _push(task_id, "usage", usage)
            usage, content = await asyncio.wait_for(
                _call_model(cfg, prompt, record_usage), timeout=REQUEST_TIMEOUT)
        finally:
            ticker.cancel()
            try:
                await ticker
            except asyncio.CancelledError:
                pass

        await _progress(task_id, 0.7, "模型已返回，正在解析校验…")
        problem = parse_problem(content.replace(cfg["api_key"], "***"))
        if problem_id:
            problem["id"] = problem_id

        await _progress(task_id, 0.9, "校验通过，正在保存结果…")
        if task_id in _cancelled:
            return
        async with SessionLocal() as db:
            t = await db.get(AiTask, task_id)
            if t is None or t.status in TERMINAL_STATUSES or task_id in _cancelled:
                return
            t.status, t.progress, t.result, t.usage = STATUS_DONE, 1.0, problem, usage
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
        if isinstance(e, asyncio.TimeoutError):
            msg = (
                f"model request timed out after {REQUEST_TIMEOUT:g}s; "
                "you can increase OJ_AI_REQUEST_TIMEOUT for slower reasoning models"
            )
        else:
            msg = _sanitize(str(e), secret) or "model request failed without an error message"
        if task_id not in _cancelled:
            try:
                async with SessionLocal() as db:
                    t = await db.get(AiTask, task_id)
                    if t is not None and t.status not in TERMINAL_STATUSES and task_id not in _cancelled:
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
        # 每个 SSE 订阅者负责释放自己的队列；取消接口仍需向队列广播终态。


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
    cfg = apply_generation_mode(cfg, body.generation_mode)
    return await _enqueue_task(
        user.id, body.requirement, body.problem_id, cfg, body.generation_mode,
    )


async def _enqueue_task(
    user_id: int,
    requirement: str,
    problem_id: str | None,
    cfg: dict,
    generation_mode: str | None = None,
) -> AiTask:
    """创建一条新任务并调度执行；新建和失败重试共用此路径。"""
    async with SessionLocal() as db:
        task = AiTask(
            user_id=user_id, requirement=requirement, problem_id=problem_id,
            status=STATUS_PENDING, progress=0.0,
            provider_url=cfg["provider_url"], model=cfg["model"],
            generation_mode=generation_mode,
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        task_id = task.id
    _cancelled.discard(task_id)
    # 固定本次调用的 URL、模型、密钥和价格，配置变更只作用于后续任务。
    _tasks[task_id] = asyncio.create_task(_run_task(task_id, cfg))
    return task


async def retry_task(user: User, task_id: int) -> AiTask:
    """从失败任务创建新任务，保留原失败原因及已产生的用量。"""
    async with SessionLocal() as db:
        source = await db.get(AiTask, task_id)
        if source is None:
            raise ApiError(404, "task not found")
        if user.role != "admin" and source.user_id != user.id:
            raise ApiError(403, "permission denied")
        if source.status != STATUS_FAILED:
            raise ApiError(409, "only failed tasks can be retried")
        owner_id = source.user_id
        requirement = source.requirement
        problem_id = source.problem_id
        generation_mode = source.generation_mode

    cfg = await config_store.load()
    if cfg is None:
        raise ApiError(400, "model config not set")
    if problem_id:
        try:
            await store.get(problem_id)
        except ApiError as exc:
            if exc.status == 404:
                raise ApiError(404, "problem not found")
            raise
    # 新版任务沿用原模式；旧任务在 DeepSeek 官方接口下默认使用均衡模式。
    if generation_mode is None and supports_generation_modes(cfg):
        generation_mode = "balanced"
    cfg = apply_generation_mode(cfg, generation_mode)
    return await _enqueue_task(owner_id, requirement, problem_id, cfg, generation_mode)


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
    _cancelled.add(task_id)
    task = _tasks.get(task_id)
    if task is not None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    async with SessionLocal() as db:
        t = await db.get(AiTask, task_id)
        if t.status in TERMINAL_STATUSES:
            raise ApiError(409, "task already finished")
        t.status = STATUS_CANCELLED
        await db.commit()
    # 立即向所有观察者推送终态（advance.md R3：界面明确展示任务已中断的状态；
    # _run_task 被 cancel 后不再推送，避免与这里的 final 重复）
    await _push(task_id, "final", {
        "task_id": task_id, "status": STATUS_CANCELLED, "message": "任务已中断",
    })
    return STATUS_CANCELLED


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def sse_stream(state: dict, task_id: int):
    """SSE 进度事件流（权限校验已在路由层完成，避免响应开始后才抛异常）。

    先发当前状态，再推送 progress/final 事件；15s 心跳保活。
    """
    q = asyncio.Queue(maxsize=64)
    queues = _events.setdefault(task_id, set())
    queues.add(q)
    try:
        # 状态查询与订阅之间任务可能已经结束，必须重新读取一次以免漏掉终态。
        async with SessionLocal() as db:
            task = await db.get(AiTask, task_id)
            if task is not None:
                state = _serialize(task)
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
    finally:
        queues.discard(q)
        if not queues and _events.get(task_id) is queues:
            _events.pop(task_id, None)


async def shutdown() -> None:
    tasks = list(_tasks.values())
    _cancelled.update(_tasks)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _tasks.clear()
    _events.clear()
    _cancelled.clear()


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
