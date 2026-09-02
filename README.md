# Online Judge（实验二：在线评测系统）

基于 FastAPI 异步接口的在线评测系统。**Step 1–6 已全部实现**：
题目管理、评测引擎（沙箱判题）、评测管理（提交/重判/限流）、
用户与权限管理、评测日志（明细可见性 + 访问审计）与配套前端页面。
Advance AI 命题模块留有清晰扩展点。

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
| 认证 | Cookie Session + bcrypt（兼容旧 PBKDF2 哈希） | 与 login/logout API 天然对应 |
| 前端 | 静态 HTML/JS（无构建步骤） | 离线验收环境可直接运行；API 与前端解耦，随时可换 Vue/React |
| 判题沙箱 | subprocess + `resource.setrlimit`（CPU/内存/进程数）+ psutil 内存监控 + 墙钟兜底 | 见 `app/judge/runner.py` 设计说明 |

## 目录结构

```
app/
├── main.py              # 应用入口：路由装配、生命周期、静态页面
├── config.py            # 全局配置（路径/限制/初始管理员/限流）
├── database.py          # 异步引擎与会话（换数据库只改这里）
├── models/              # ORM 模型：User/Session/Submission/TestcaseResult/
│                        #   Language/AccessLog/RoleChangeLog/AiTask
├── schemas/             # Pydantic 请求模型（字段与 api.md 一致）
├── core/                # 核心设施
│   ├── errors.py        #   统一异常：所有错误返回 {code, msg, data}
│   ├── deps.py          #   认证依赖：get_current_user / require_admin
│   ├── security.py      #   密码哈希（bcrypt + 旧格式兼容）
│   └── rate_limit.py    #   提交限流（429）
├── services/            # 业务逻辑层
│   ├── problem_store.py #   题目 JSON 文件存储（Step 1）
│   ├── user_service.py  #   用户注册/校验/统计/初始管理员
│   ├── language_service.py # 默认语言种子（Step 2）
│   └── judge_service.py #   异步评测编排/重判代际/重启恢复（Step 3）
├── routers/             # 路由层（按业务域划分）
│   ├── problems.py      #   Step 1 题目 CRUD + Step 5 log_visibility ✅
│   ├── auth.py          #   Step 4 登录/登出 ✅
│   ├── users.py         #   Step 4 注册/角色/用户查询/操作日志 ✅
│   ├── languages.py     #   Step 2 语言注册表（登录用户可注册） ✅
│   ├── submissions.py   #   Step 3 提交/列表/详情/rejudge/限流 + Step 5 log ✅
│   ├── logs.py          #   Step 5 访问审计 ✅
│   ├── maintenance.py   #   测试辅助 /api/reset/ ✅
│   └── ai.py            #   Advance AI 命题（骨架，待实现）
└── judge/
    └── runner.py        # 判题引擎：沙箱执行/资源限制/输出比对（Step 2） ✅

data/problems/           # 题目配置文件（sum_2 / P1001 示例）
static/                  # 前端（题目/提交面板/评测列表详情/用户管理，hash 路由）
tests/                   # pytest 接口测试（12 个，含真实判题端到端）
```

## 分层约定（扩展方式）

```
routers  →  HTTP 语义（路径/参数/状态码），只做转发
services →  业务逻辑（校验/存储/统计/编排）
models   →  数据结构（ORM / 文件）
```

**新增功能的标准做法**：在对应域下写 service → 写 router → 在 `main.py` 挂载
→ 前端加视图（`static/js/views/`）+ 注册路由（`app.js` 的 routes）。

## 评测流水线（Step 2/3）

提交后立即返回 `pending`，后台 `asyncio.create_task` 执行评测：

1. 编译（有 `compile_cmd` 的语言）失败 → `CE`；
2. 逐测试点执行：`RLIMIT_CPU`（超时→TLE）+ 内存监控（超限→MLE）+ 非零退出→RE，
   输出逐行比对（忽略行尾空白与末尾空行）→ AC/WA；
3. 每个测试点 10 分，`score = AC 数 × 10`。

重判通过**代际机制**防止旧评测任务覆盖新结果；服务重启时自动重判遗留 pending 提交。

## 关键约定（来自实验要求）

- **全部接口必须使用 FastAPI 异步接口**（`async def`），否则无法得分；
- 响应格式统一 `{code, msg, data}`，异常处理顺序 401 > 403 > 400 > 429 > 409 > 404 > 500；
- 权限判断全部在后端完成，前端展示的登录态仅供参考；
- Git 提交遵循 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/v1.0.0/)，
  大文件不入库（数据库文件已 gitignore）。

## 待扩展（Advance）

AI 命题模块：实现 `app/routers/ai.py`（骨架与 api.md 规格注释已就绪），
`AiTask` 模型已建表，密钥安全要求见该文件头部注释；挂载取消 `main.py` 中的注释即可。
