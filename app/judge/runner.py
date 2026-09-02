"""判题执行器（Step 2）。

实现方案（结合 Step 2 文档与 FAQ 建议）：

1. 沙箱执行
   - subprocess.Popen + preexec_fn 中调用 resource.setrlimit：
       RLIMIT_CPU   → CPU 时间限制（超时 → TLE，SIGXCPU/SIGKILL）
       RLIMIT_FSIZE → 输出大小限制（防止刷爆磁盘）
       RLIMIT_NPROC → 子进程数限制
   - 墙钟超时用 communicate(timeout=...) 兜底（sleep 类程序）；
   - psutil 监控线程（FAQ 推荐）：轮询进程树内存峰值，超限即杀（MLE），
     并统计峰值内存（KB → MB）。
   - 所有阻塞调用经 asyncio.to_thread 放入线程池，接口保持异步。

2. 编译
   - 从 languages 表读取 compile_cmd 模板，替换 {src}/{exe}；
     解释型语言（compile_cmd 为空）跳过编译；
   - 编译失败 → CE（compile_info 记录编译器输出，截断防刷屏）。

3. 执行与比对
   - 逐测试点：stdin 重定向测试点输入，run_cmd 模板替换后执行；
   - OutputComparer 逐行比较、忽略每行行尾空白与最后多余空行（Step 2 要求）；
   - 运行错误（非零退出）→ RE，错误信息记录 stderr（截断、脱敏）。

4. 结果集合（Step 2）：AC / WA / TLE / MLE / RE / CE / UNK
   - 编译失败整体记 CE；其余按测试点判定；无法归类 → UNK。
"""
import asyncio
import resource
import shlex
import subprocess
import threading
import time
from pathlib import Path

import psutil

from app import config

AC, WA, TLE, MLE, RE, CE, UNK = "AC", "WA", "TLE", "MLE", "RE", "CE", "UNK"

COMPILE_TIMEOUT = 30                # 编译墙钟超时（秒）
MAX_OUTPUT_BYTES = 32 * 1024 * 1024  # 单次运行输出上限（配合 RLIMIT_FSIZE）
INFO_LIMIT = 4096                   # 编译/错误信息截断长度
MEMORY_POLL_INTERVAL = 0.01         # psutil 内存监控轮询间隔（秒）


class JudgeResult:
    """单个测试点的评测结果。"""

    __slots__ = ("case_id", "result", "time", "memory", "detail")

    def __init__(self, case_id: str, result: str, time: float = 0.0, memory: float = 0.0, detail: str = ""):
        self.case_id = case_id
        self.result = result      # AC / WA / TLE / MLE / RE / UNK
        self.time = time          # 墙钟时间（秒）
        self.memory = memory      # 峰值内存（MB）
        self.detail = detail      # 差异说明 / 错误信息（已脱敏截断）


def _set_limits(cpu_seconds: float, max_bytes: int, nproc: int = 4096):
    """preexec_fn：在子进程中收紧资源限制。

    nproc：RLIMIT_NPROC 在 Linux 上按「全系统该 UID 的进程/线程总数」计数
    （VSCode/终端等多线程应用很容易就占数百），过低会让 g++ 等正常程序
    fork/vfork 子进程时直接 EAGAIN。取 4096 避免误伤；
    fork bomb 由 RLIMIT_CPU 兜底（炸弹会迅速烧满 CPU 配额被 SIGXCPU 杀掉）。
    """

    def _apply() -> None:
        resource.setrlimit(resource.RLIMIT_CPU, (int(cpu_seconds) + 1, int(cpu_seconds) + 2))
        resource.setrlimit(resource.RLIMIT_FSIZE, (max_bytes, max_bytes))
        resource.setrlimit(resource.RLIMIT_NPROC, (nproc, nproc))

    return _apply


def _memory_monitor(proc: subprocess.Popen, limit_bytes: int, stop: threading.Event,
                    peak_mb: list, killed: list) -> None:
    """psutil 监控线程：统计进程树峰值内存，超限立即杀进程。"""
    try:
        p = psutil.Process(proc.pid)
        while not stop.is_set():
            try:
                rss = p.memory_info().rss
                for child in p.children(recursive=True):
                    rss += child.memory_info().rss
            except psutil.NoSuchProcess:
                return
            peak_mb[0] = max(peak_mb[0], rss / (1024 * 1024))
            if rss > limit_bytes:
                killed[0] = True
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                return
            time.sleep(MEMORY_POLL_INTERVAL)
    except psutil.Error:
        return


def _execute(cmd: list[str], stdin_bytes: bytes, time_limit: float, mem_limit_bytes: int,
             workdir: Path) -> tuple:
    """阻塞执行一个命令。返回 (returncode|None, stdout, stderr, wall_time, peak_mb, killed_by_mem, timed_out)。"""
    peak_mb = [0.0]
    killed = [False]
    stop = threading.Event()

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(workdir),
            preexec_fn=_set_limits(time_limit, MAX_OUTPUT_BYTES),
        )
    except OSError as e:
        return None, b"", str(e).encode(errors="replace"), 0.0, 0.0, False, False

    monitor = threading.Thread(
        target=_memory_monitor, args=(proc, mem_limit_bytes, stop, peak_mb, killed), daemon=True
    )
    monitor.start()

    timed_out = False
    start = time.perf_counter()
    try:
        stdout, stderr = proc.communicate(stdin_bytes, timeout=time_limit)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        stdout, stderr = proc.communicate()
    wall = time.perf_counter() - start

    stop.set()
    monitor.join(timeout=1)

    return proc.returncode, stdout, stderr, wall, peak_mb[0], killed[0], timed_out


class OutputComparer:
    """输出比对（Step 2：忽略多余的行末空格和最后一行多余换行）。"""

    @staticmethod
    def _normalize(text: str) -> list[str]:
        lines = text.replace("\r\n", "\n").split("\n")
        while lines and lines[-1].strip() == "":
            lines.pop()
        return [line.rstrip() for line in lines]

    @staticmethod
    def compare(expected: str, actual: str) -> tuple[bool, str]:
        """返回 (是否一致, 差异说明)。"""
        if OutputComparer._normalize(expected) == OutputComparer._normalize(actual):
            return True, ""
        return False, f"expected:\n{expected[:200]}\n---\ngot:\n{actual[:200]}"


class JudgeRunner:
    """一次评测的执行器：编译 + 逐测试点运行。

    language / problem 为 dict；限制解析顺序：语言配置 → 题目配置 → 全局默认。
    """

    def __init__(self, language: dict, problem: dict, workdir: Path):
        self.language = language
        self.problem = problem
        self.workdir = workdir
        self.time_limit = float(
            language.get("time_limit") or problem.get("time_limit") or config.DEFAULT_TIME_LIMIT
        )
        self.memory_limit = int(
            language.get("memory_limit") or problem.get("memory_limit") or config.DEFAULT_MEMORY_LIMIT
        )
        ext = language["file_ext"]
        if ext and not ext.startswith("."):   # g++ 等编译器按扩展名识别文件类型，必须带点
            ext = "." + ext
        self._src = workdir / f"main{ext}"
        self._exe = workdir / "main"

    # ---- 命令模板展开与脱敏 ----

    def _expand(self, cmd: str) -> list[str]:
        return shlex.split(cmd.replace("{src}", str(self._src)).replace("{exe}", str(self._exe)))

    def _sanitize(self, text: str) -> str:
        """截断并按掉临时目录路径，避免泄露敏感路径（api.md）。"""
        text = text.replace(str(self.workdir), "<workdir>")
        return text[-INFO_LIMIT:]

    # ---- 编译 ----

    async def compile(self, code: str) -> tuple[bool, str | None]:
        """编译源码。返回 (是否成功, 编译输出；解释型语言为 None)。"""
        self._src.write_text(code, encoding="utf-8")
        cmd = self.language.get("compile_cmd")
        if not cmd:
            return True, None

        def _run() -> tuple[bool, str]:
            try:
                proc = subprocess.Popen(
                    self._expand(cmd),
                    cwd=str(self.workdir),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    # 编译阶段同样收紧资源：CPU 时间、产物大小（.o/.exe）、子进程数
                    preexec_fn=_set_limits(COMPILE_TIMEOUT, MAX_OUTPUT_BYTES),
                )
                out, err = proc.communicate(timeout=COMPILE_TIMEOUT)
                text = (out + err).decode(errors="replace")
                return proc.returncode == 0, self._sanitize(text)
            except subprocess.TimeoutExpired:
                return False, "compile timeout"
            except OSError as e:
                return False, str(e)

        return await asyncio.to_thread(_run)

    # ---- 单测试点运行 ----

    async def run_case(self, case: dict, index: int) -> JudgeResult:
        """运行单个测试点：执行 + 资源限制 + 输出比对。"""
        case_id = str(case.get("id") or index + 1)
        cmd = self._expand(self.language["run_cmd"])

        rc, stdout, stderr, wall, peak_mb, killed_mem, timed_out = await asyncio.to_thread(
            _execute, cmd, case["input"].encode(), self.time_limit,
            self.memory_limit * 1024 * 1024, self.workdir,
        )

        if rc is None:
            return JudgeResult(case_id, UNK, 0.0, 0.0, "cannot start process")

        actual = stdout.decode(errors="replace")
        detail = self._sanitize(stderr.decode(errors="replace"))

        # 结果判定
        if killed_mem:
            return JudgeResult(case_id, MLE, round(wall, 3), round(peak_mb, 1), detail)
        if timed_out or rc in (-9, -24):   # SIGKILL / SIGXCPU
            return JudgeResult(case_id, TLE, round(wall, 3), round(peak_mb, 1), detail)
        if rc != 0:
            return JudgeResult(case_id, RE, round(wall, 3), round(peak_mb, 1), detail or f"exit code {rc}")

        matched, diff = OutputComparer.compare(case["output"], actual)
        if matched:
            return JudgeResult(case_id, AC, round(wall, 3), round(peak_mb, 1))
        return JudgeResult(case_id, WA, round(wall, 3), round(peak_mb, 1), self._sanitize(diff))
