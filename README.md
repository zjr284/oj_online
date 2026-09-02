# Online Judge（实验二：在线评测系统）

基于 FastAPI 异步接口的在线评测系统。当前为**可运行的初始架构**：
Step 1（题目管理）与 Step 4（用户管理核心）已完整实现，
Step 2/3/5 与 AI 进阶模块留有清晰的扩展点，可逐步添加。

## 快速开始

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload   # 或 ./run.sh
```

打开 http://127.0.0.1:8000 ，初始管理员：`admin` / `admintestpassword`。

运行测试：

```bash
.venv/bin/pytest -q
```

## 技术栈与理由

| 组件 | 选型 | 理由 |
|---|---|---|
| Web 框架 | FastAPI（**全程 `async def`**） | 实验硬性要求，不用异步接口无法得分 |
| 数据库 | SQLite + SQLAlchemy 2.0 async（aiosqlite） | 零部署成本，验收环境开箱即用；换 Postgres 只改 `config.DB_URL` |
| 题目存储 | JSON 文件（`data/problems/`，每题一个） | Step 1 实验要求；`ProblemStore` 抽象后可换数据库 |
| 认证 | Cookie Session + PBKDF2-SHA256（标准库） | 与 login/logout API 天然对应；无额外依赖 |
| 前端 | 静态 HTML/JS（无构建步骤） | 离线验收环境可直接运行；API 与前端解耦，随时可换 Vue/React |
| 判题沙箱 | subprocess + `resource.setrlimit` + psutil 监控 | 见 `app/judge/runner.py` 设计说明 |

## 目录结构

```
app/
├── main.py              # 应用入口：路由装配、生命周期、静态页面
├── config.py            # 全局配置（路径/限制/初始管理员）
├── database.py          # 异步引擎与会话（换数据库只改这里）
├── models/              # ORM 模型：User/Session/Submission/TestcaseResult/
│                        #   Language/AccessLog/AiTask（全部已建表）
├── schemas/             # Pydantic 请求模型（字段与 api.md 一致）
├── core/                # 核心设施
│   ├── errors.py        #   统一异常：所有错误返回 {code, msg, data}
│   ├── deps.py          #   认证依赖：get_current_user / require_admin
│   ├── security.py      #   密码哈希、令牌
│   └── rate_limit.py    #   提交限流（429）
├── services/            # 业务逻辑层
│   ├── problem_store.py #   题目 JSON 文件存储（Step 1）
│   └── user_service.py  #   用户注册/统计/初始管理员
├── routers/             # 路由层（按业务域划分）
│   ├── problems.py      #   Step 1 题目 CRUD + Step 5 log_visibility ✅
│   ├── auth.py          #   Step 4 登录/登出 ✅
│   ├── users.py         #   Step 4 注册/角色/用户查询 ✅
│   ├── languages.py     #   Step 2 语言注册表 ✅
│   ├── maintenance.py   #   测试辅助 /api/reset/ ✅
│   ├── submissions.py   #   Step 3 评测管理（骨架，待实现）
│   ├── logs.py          #   Step 5 评测日志（骨架，待实现）
│   └── ai.py            #   Advance AI 命题（骨架，待实现）
└── judge/               # 判题引擎（Step 2，含实现方案说明）

data/problems/           # 题目配置文件（sum_2 / P1001 示例）
static/                  # 前端（题目/登录/注册/用户页面可用，评测页占位）
tests/                   # pytest 接口测试
```

## 分层约定（扩展方式）

```
routers  →  HTTP 语义（路径/参数/状态码），只做转发
services →  业务逻辑（校验/存储/统计）
models   →  数据结构（ORM / 文件）
```

**新增功能的标准做法**：在对应域下写 service → 写 router → 在 `main.py` 挂载
→ 前端加视图（`static/js/views/`）+ 注册路由（`app.js` 的 routes）。

## 各 Step 实现指引

- **Step 2 评测控制**：实现 `app/judge/runner.py`（沙箱/编译/比对方案已写在该文件头部）；
  语言注册表已可用（`POST /api/languages/`）。
- **Step 3 评测管理**：实现 `app/routers/submissions.py`（接口规格已注释在文件头部）；
  提交后用 `asyncio.create_task` 异步评测，限流用 `core/rate_limit.py`。
- **Step 5 评测日志**：实现 `app/routers/logs.py` + submissions 明细接口；
  数据表 `testcase_results`/`access_logs` 与题目的 `public_cases` 字段均已就绪。
- **Advance AI 命题**：实现 `app/routers/ai.py`；任务模型 `AiTask` 已建表，
  密钥安全要求见该文件头部注释。

## 关键约定（来自实验要求）

- **全部接口必须使用 FastAPI 异步接口**（`async def`），否则无法得分；
- 响应格式统一 `{code, msg, data}`，异常处理顺序 401 > 403 > 400 > 429 > 409 > 404 > 500；
- 权限判断全部在后端完成，前端展示的登录态仅供参考；
- Git 提交遵循 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/v1.0.0/)，
  大文件不入库（数据库文件已 gitignore）。
