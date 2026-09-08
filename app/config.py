"""全局配置：集中管理路径、默认值与业务参数。

新增配置项统一放在这里，避免散落在各模块中。
"""
import math
import os
from pathlib import Path

# 项目根目录（app/ 的上一级）
BASE_DIR = Path(__file__).resolve().parent.parent

# 数据目录：可通过环境变量 OJ_DATA_DIR 覆盖（测试时指向临时目录）
DATA_DIR = Path(os.environ.get("OJ_DATA_DIR", BASE_DIR / "data"))

# 题目配置目录（Step 1：每题一个 JSON 文件）
PROBLEMS_DIR = DATA_DIR / "problems"

# 数据库连接：默认 SQLite（零部署成本）；换 Postgres 只需改这里 + 驱动
DB_URL = f"sqlite+aiosqlite:///{DATA_DIR / 'oj.db'}"

# 题目默认限制（api.md）
DEFAULT_TIME_LIMIT = 3       # 秒
DEFAULT_MEMORY_LIMIT = 128   # MB

# 初始管理员（api.md：系统自动创建 admin / admintestpassword）
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admintestpassword"

# 会话有效期（秒）
SESSION_TTL_SECONDS = 7 * 24 * 3600

# AI 推理模型可能需要数分钟生成完整题面和测试点。允许部署时覆盖，
# 但对非法值安全回退，避免因环境变量写错导致服务无法启动。
try:
    AI_REQUEST_TIMEOUT_SECONDS = float(os.environ.get("OJ_AI_REQUEST_TIMEOUT", 600))
except ValueError:
    AI_REQUEST_TIMEOUT_SECONDS = 600.0
if not math.isfinite(AI_REQUEST_TIMEOUT_SECONDS) or AI_REQUEST_TIMEOUT_SECONDS <= 0:
    AI_REQUEST_TIMEOUT_SECONDS = 600.0

# 默认不继承 HTTP_PROXY/HTTPS_PROXY。桌面环境常遗留指向已停止
# 本地代理的变量，会使配置正确的模型 URL 立即连接失败。
# 部署确实需要系统代理时可显式设为 1/true/yes/on。
AI_TRUST_ENV = os.environ.get("OJ_AI_TRUST_ENV", "").strip().lower() in {
    "1", "true", "yes", "on",
}

# 提交频率限制（api.md：1 分钟内提交超过 3 次 → 429）
# 测试时可通过环境变量 OJ_SUBMIT_RATE_LIMIT 调高
SUBMIT_RATE_LIMIT = int(os.environ.get("OJ_SUBMIT_RATE_LIMIT", 3))
SUBMIT_RATE_WINDOW = 60     # 秒
