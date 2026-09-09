"""异步判题：资源监控、进程组清理、有界输出及逐行比对。

使用本机 Python / C++ 工具链，适用于课程本地验收。
资源限制不提供容器级文件系统或网络隔离。
"""
import asyncio
import math
import os
import resource
import shlex
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import psutil

from app import config

AC, WA, TLE, MLE, RE, CE, UNK = "AC", "WA", "TLE", "MLE", "RE", "CE", "UNK"
COMPILE_TIMEOUT = 30
COMPILE_MEMORY_BYTES = 512 * 1024 * 1024
MAX_OUTPUT_BYTES = 32 * 1024 * 1024
INFO_LIMIT = 4096
MEMORY_POLL_INTERVAL = 0.01


class JudgeResult:
    """单个测试点的判定结果、耗时、峰值内存与诊断信息。"""
    __slots__ = ("case_id", "result", "time", "memory", "detail")

    def __init__(self, case_id, result, time=0.0, memory=0.0, detail=""):
        """用评测循环收集到的原始值构造轻量结果对象。"""
        self.case_id, self.result = case_id, result
        self.time, self.memory, self.detail = time, memory, detail


def _set_limits(cpu_seconds: float, max_bytes: int, nproc: int = 4096):
    """返回子进程启动钩子，设置 CPU、输出、进程数和 core 限制。"""
    def apply():
        """由 ``Popen(preexec_fn=...)`` 在子进程执行限制设置。"""
        cpu = max(1, math.ceil(cpu_seconds))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
        resource.setrlimit(resource.RLIMIT_FSIZE, (max_bytes, max_bytes))
        # NPROC 按 UID 计数，不能把开发环境中的其他线程当作提交创建的进程。
        soft, hard = resource.getrlimit(resource.RLIMIT_NPROC)
        ceiling = nproc if hard == resource.RLIM_INFINITY else min(nproc, hard)
        resource.setrlimit(resource.RLIMIT_NPROC, (ceiling, ceiling))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    return apply


def _kill_group(proc):
    """终止评测进程组，防止代码派生的子进程遗留。"""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _execute(cmd, stdin_bytes, time_limit, mem_limit_bytes, workdir, cancelled=None):
    """阻塞执行，所有退出路径均清理进程组；文件输出受 FSIZE 限制。

    返回 (returncode, stdout, stderr, wall_time, peak_mb, killed_by_mem, timed_out)。
    """
    peak_mb, killed_mem, timed_out = 0.0, False, False
    cancelled = cancelled or threading.Event()
    # 不把后端的模型密钥、代理凭据或其他环境变量传给用户程序。
    env = {"PATH": os.environ.get("PATH", os.defpath), "LANG": "C.UTF-8", "PYTHONIOENCODING": "utf-8",
           "TMPDIR": str(workdir)}
    with tempfile.TemporaryFile(dir=workdir) as inp, \
            tempfile.TemporaryFile(dir=workdir) as out, \
            tempfile.TemporaryFile(dir=workdir) as err:
        inp.write(stdin_bytes)
        inp.seek(0)
        if cancelled.is_set():
            return None, b"", b"cancelled", 0.0, 0.0, False, False
        start = time.perf_counter()
        try:
            proc = subprocess.Popen(cmd, stdin=inp, stdout=out, stderr=err,
                                    cwd=workdir, env=env, start_new_session=True,
                                    preexec_fn=_set_limits(time_limit, MAX_OUTPUT_BYTES))
        except (OSError, ValueError) as exc:
            return None, b"", str(exc).encode(errors="replace"), 0.0, 0.0, False, False
        try:
            p = psutil.Process(proc.pid)
            while proc.poll() is None:
                rss = 0
                try:
                    processes = [p, *p.children(recursive=True)]
                except psutil.Error:
                    processes = [p]
                for child in processes:
                    try:
                        rss += child.memory_info().rss
                    except psutil.Error:
                        pass
                peak_mb = max(peak_mb, rss / (1024 * 1024))
                killed_mem = rss > mem_limit_bytes
                timed_out = time.perf_counter() - start >= time_limit
                if cancelled.is_set() or killed_mem or timed_out:
                    break
                cancelled.wait(MEMORY_POLL_INTERVAL)
        finally:
            # 即使父进程正常退出，也不允许其后台子进程存活或占住输出管道。
            _kill_group(proc)
            proc.wait()
        wall = time.perf_counter() - start
        out.seek(0)
        err.seek(0)
        return (proc.returncode, out.read(MAX_OUTPUT_BYTES), err.read(INFO_LIMIT),
                wall, peak_mb, killed_mem, timed_out)


async def _execute_async(*args):
    """在线程中执行阻塞评测；协程取消时等待线程完成清理。"""
    cancelled = threading.Event()
    worker = asyncio.create_task(asyncio.to_thread(_execute, *args, cancelled))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        cancelled.set()
        # 先等执行线程清理子进程，再允许评测任务删除工作目录。
        await asyncio.shield(worker)
        raise


class OutputComparer:
    """输出比较器：忽略行尾空白和结尾的空行。"""
    @staticmethod
    def _normalize(text: str) -> list[str]:
        """统一换行并清除允许忽略的尾随空白。"""
        lines = text.replace("\r\n", "\n").split("\n")
        while lines and not lines[-1].strip():
            lines.pop()
        return [line.rstrip() for line in lines]

    @staticmethod
    def compare(expected: str, actual: str) -> tuple[bool, str]:
        """比较标准输出与实际输出；失败时返回截断后的差异。"""
        if OutputComparer._normalize(expected) == OutputComparer._normalize(actual):
            return True, ""
        return False, f"expected:\n{expected[:200]}\n---\ngot:\n{actual[:200]}"


class JudgeRunner:
    """优先级：题目显式限制 → 语言配置 → 系统默认。"""
    def __init__(self, language: dict, problem: dict, workdir: Path):
        """固化本次评测的语言、题目限制和独立工作目录。"""
        self.language, self.problem, self.workdir = language, problem, workdir
        self.time_limit = float(problem.get("time_limit") or language.get("time_limit")
                                or config.DEFAULT_TIME_LIMIT)
        self.memory_limit = int(problem.get("memory_limit") or language.get("memory_limit")
                                or config.DEFAULT_MEMORY_LIMIT)
        ext = language["file_ext"]
        if not ext.startswith("."):
            ext = "." + ext
        self._src, self._exe = workdir / f"main{ext}", workdir / "main"

    def _expand(self, cmd: str) -> list[str]:
        """展开源码/可执行文件占位符并分词，始终不经过 shell。"""
        # 先分词再替换，含空格的工作目录仍是一个参数；永不经 shell 执行。
        return [part.replace("{src}", str(self._src)).replace("{exe}", str(self._exe))
                for part in shlex.split(cmd)]

    def _sanitize(self, text: str) -> str:
        """移除临时目录路径并限制诊断文本长度。"""
        return text.replace(str(self.workdir), "<workdir>")[-INFO_LIMIT:]

    async def compile(self, code: str) -> tuple[bool, str | None]:
        """写入源文件并执行可选编译命令，返回成功标志和编译信息。"""
        await asyncio.to_thread(self._src.write_text, code, encoding="utf-8")
        cmd = self.language.get("compile_cmd")
        if not cmd:
            return True, None
        rc, out, err, _, _, memory, timeout = await _execute_async(
            self._expand(cmd), b"", COMPILE_TIMEOUT, COMPILE_MEMORY_BYTES, self.workdir)
        if timeout:
            return False, "compile timeout"
        if memory:
            return False, "compiler memory limit exceeded"
        return rc == 0, self._sanitize((out + err).decode(errors="replace"))

    async def run_case(self, case: dict, index: int) -> JudgeResult:
        """运行一个测试点，映射资源/退出/输出结果为 OJ 判定。"""
        case_id = str(case.get("id") or index + 1)
        rc, out, err, wall, peak, memory, timeout = await _execute_async(
            self._expand(self.language["run_cmd"]), case["input"].encode(),
            self.time_limit, self.memory_limit * 1024 * 1024, self.workdir)
        detail = self._sanitize(err.decode(errors="replace"))
        if rc is None:
            result, detail = UNK, "cannot start process"
        elif memory:
            result = MLE
        elif timeout or rc == -signal.SIGXCPU:
            result = TLE
        elif rc != 0:
            result, detail = RE, detail or f"exit code {rc}"
        else:
            matched, diff = OutputComparer.compare(case["output"], out.decode(errors="replace"))
            result, detail = (AC, "") if matched else (WA, self._sanitize(diff))
        return JudgeResult(case_id, result, round(wall, 3), round(peak, 1), detail)
