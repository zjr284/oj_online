# Online Judge（实验二：在线评测系统）

基于 FastAPI 异步接口的在线评测系统。**Step 1–6 与 Advance AI 智能命题已全部实现**：
题目管理、评测引擎（沙箱判题）、评测管理（提交/重判/限流）、
用户与权限管理、评测日志（明细可见性 + 访问审计）、
AI 命题（可配置模型/实时进度/中断/用量计费）与配套前端页面。

## 快速开始

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
./run.sh                                    # 一键启动：后端 API(8000) + Streamlit 前端(8501)
```

打开 http://127.0.0.1:8501 （Streamlit 前端），初始管理员：`admin` / `admintestpassword`。
后端为纯 API 服务（http://127.0.0.1:8000 ）。

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
| 前端 | Streamlit（app.py，step6.md 要求） | 与 API 解耦（Cookie 经 httpx 传递）；离线验收环境可直接运行 |
| 判题沙箱 | subprocess + `resource.setrlimit`（CPU/内存/进程数）+ psutil 内存监控 + 墙钟兜底 | 见 `app/judge/runner.py` 设计说明 |

## 目录结构

```
app/
├── main.py              # 应用入口：路由装配、生命周期（纯 API）
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
│   ├── judge_service.py #   异步评测编排/重判代际/重启恢复（Step 3）
│   └── ai_service.py    #   AI 命题：加密配置/任务编排/模型调用/用量计费（Advance）
├── routers/             # 路由层（按业务域划分）
│   ├── problems.py      #   Step 1 题目 CRUD + Step 5 log_visibility ✅
│   ├── auth.py          #   Step 4 登录/登出 ✅
│   ├── users.py         #   Step 4 注册/角色/用户查询/操作日志 ✅
│   ├── languages.py     #   Step 2 语言注册表（登录用户可注册） ✅
│   ├── submissions.py   #   Step 3 提交/列表/详情/rejudge/限流 + Step 5 log ✅
│   ├── logs.py          #   Step 5 访问审计 ✅
│   ├── maintenance.py   #   测试辅助 /api/reset/ ✅
│   └── ai.py            #   Advance AI 命题：配置/任务/SSE 进度/取消 ✅
└── judge/
    └── runner.py        # 判题引擎：沙箱执行/资源限制/输出比对（Step 2） ✅

data/problems/           # 题目配置文件（sum_2 / P1001 示例）
app.py                   # Streamlit 前端（题目/评测/用户管理/访问审计/AI 命题页面）
tests/                   # pytest 接口测试（202 个，含真实判题端到端、AI 全流程与 Streamlit 冒烟）
```

## 分层约定（扩展方式）

```
routers  →  HTTP 语义（路径/参数/状态码），只做转发
services →  业务逻辑（校验/存储/统计/编排）
models   →  数据结构（ORM / 文件）
```

**新增功能的标准做法**：在对应域下写 service → 写 router → 在 `main.py` 挂载
→ Streamlit 前端在 `app.py` 加页面函数，并在 `render_sidebar` 导航与 `main()` 分发中注册。

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

## Advance：AI 智能命题

接口（api.md 建议路径 + 等价扩展，已在文档说明）：

| 接口 | 说明 |
|---|---|
| `PUT /api/ai/model-config` | 配置 provider_url/model/api_key/价格（登录用户）；**api_key 加密存储且永不返回** |
| `GET /api/ai/model-config` | 查询配置公开字段（等价扩展；仍不返回 api_key） |
| `POST /api/ai/problem-tasks/` | 创建命题任务（可指定参考题目）；未配置模型 400、题目不存在 404 |
| `GET /api/ai/problem-tasks/` | 任务列表（等价扩展：本人任务，管理员全部；不含 result） |
| `GET /api/ai/problem-tasks/{id}` | 任务状态：status/progress/result/usage（创建者或管理员） |
| `GET /api/ai/problem-tasks/{id}/events` | SSE 实时进度（同时支持轮询状态接口） |
| `PUT /api/ai/problem-tasks/{id}/cancel` | 真正终止后台任务；已结束 409 |

设计要点：

- **R1 交互衔接**：前端 AI 命题页提交需求 → 实时进度 → 预览生成的题目 →
  经已有的 `POST/PUT /api/problems/` 接口导入题库，AI 模块本身不直接写题库，与基础功能解耦；
- **R2 可配置**：provider_url/model/api_key 均通过接口配置（OpenAI 兼容 chat/completions 协议，
  不写死厂商）；api_key 用 Fernet 加密存 `data/ai_config.json`（密钥文件 600 权限），
  任何接口/日志/错误信息都不泄露密钥；
- **R3 进度与中断**：SSE 推送 progress/final 事件（含心跳），断线自动退回 1.5s 轮询；
  模型调用期间每 2s 推送一次进度（progress 缓慢爬升 + "模型推理中（已 Xs）"），
  执行期间界面持续展示可观察的进度信息，而非等任务完成才返回结果；
  cancel 用 `task.cancel()` 真正终止后台任务，取消后不会被旧任务覆盖状态，
  并立即向所有 SSE 观察者推送 `final(status=cancelled)` 终态，界面明确展示已中断；
- **R4 用量计费**：`费用 = 输入Token/计价单位 × 输入单价 + 输出Token/计价单位 × 输出单价`；
  单价来源按优先级自动确定：① 模型接口在 `usage.cost` 直接返回的费用
  ② 模型配置中用户填写的输入/输出价格（前端提示：不同模型、不同时段的计费价格可能不同，
  部分厂商设有错峰优惠时段，请按实际调用时段的官方价格填写）
  ③ 未填价格且接口未返回费用时 `cost=null` 并在页面标注（`price_source: unknown`）；
  模型接口不返回用量时按字符数/4 估算，`usage.estimated=true` 并在页面标注；
- **校验入库**：模型输出必须通过 `ProblemConfig` 校验（非法 JSON/缺字段 → 任务 failed，
  错误信息脱敏）；服务重启时遗留 pending/running 任务自动标记 failed。
