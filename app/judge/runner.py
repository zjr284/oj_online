"""判题执行器（Step 2，待实现）。

实现方案（结合 FAQ 建议）：

1. 沙箱执行
   - subprocess.Popen + preexec_fn 中调用 resource.setrlimit：
       RLIMIT_CPU     → 时间限制（超时 → TLE，SIGXCPU 会被杀死）
       RLIMIT_AS      → 地址空间限制（近似内存限制 → MLE）
       RLIMIT_FSIZE   → 输出大小限制（防止刷爆磁盘）
   - 辅以 psutil 监控线程（FAQ 推荐），轮询子进程内存峰值，超限即杀，
     用于精确统计 time / memory。
   - 注意：preexec_fn 与 asyncio 不兼容时改用普通线程（asyncio.to_thread）执行。

2. 编译
   - 从 languages 表读取 compile_cmd 模板，替换 {src}/{exe} 为临时目录路径；
     解释型语言（compile_cmd 为空）跳过编译。
   - 编译失败 → status=error，compile_info 记录编译器输出。

3. 执行与比对
   - 逐测试点：stdin 重定向测试点输入，run_cmd 模板替换后执行；
   - OutputComparer 逐行比较、忽略行尾空白，WA 时记录首个差异（run_info）；
   - 运行错误（非零退出）→ RE，error_info 记录 stderr（不得泄露敏感路径）。

4. 语言注册表驱动
   - languages 表即注册表：新增语言只需 POST /api/languages/，评测代码无需改动。
"""
import asyncio
from pathlib import Path

# 待实现（Step 2）。函数签名与职责先定好，供 Step 3 评测管理调用：


class JudgeResult:
    """单个测试点的评测结果。"""
    def __init__(self, case_id: str, result: str, time: float, memory: float):
        self.case_id = case_id
        self.result = result    # AC / WA / TLE / MLE / RE
        self.time = time
        self.memory = memory


class JudgeRunner:
    """一次评测的执行器：编译 + 逐测试点运行。"""

    def __init__(self, workdir: Path, language_cfg: dict, problem_cfg: dict):
        self.workdir = workdir
        self.language_cfg = language_cfg
        self.problem_cfg = problem_cfg

    async def compile(self, code: str) -> tuple[bool, str]:
        """编译源码。返回 (是否成功, 编译输出)。"""
        raise NotImplementedError("Step 2: 实现编译")

    async def run_case(self, case: dict) -> JudgeResult:
        """运行单个测试点并比对输出。"""
        raise NotImplementedError("Step 2: 实现执行与资源限制")


class OutputComparer:
    """输出比对：默认逐行比较并忽略行尾空白；可扩展 Special Judge。"""

    @staticmethod
    def compare(expected: str, actual: str) -> tuple[bool, str]:
        """返回 (是否一致, 差异说明)。"""
        raise NotImplementedError("Step 2: 实现输出比对")
