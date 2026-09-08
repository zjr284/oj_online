# Online Judge（实验二：在线评测系统）

基于 FastAPI 异步接口的在线评测系统。**覆盖 Step 1–6 与 Advance AI 智能命题**：
题目管理、评测引擎（资源限制与进程清理）、评测管理（提交/重判/限流）、
用户与权限管理、语言查询与动态注册、评测日志（明细可见性 + 访问审计）、
AI 命题（可配置模型/实时进度/中断/用量计费）与配套前端页面。

## 快速开始

运行环境需要 Python 3.10+；C++ 评测需要 GCC/G++ 9+（内置配置使用 C++14）。

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
./run.sh                                    # 一键启动：后端 API(8000) + Streamlit 前端(8501)
```

打开 http://127.0.0.1:8501 （Streamlit 前端），初始管理员：`admin` / `admintestpassword`。
后端为纯 API 服务（http://127.0.0.1:8000 ）。

逐项核对与修复记录见 [EXPERIMENT2_AUDIT.md](EXPERIMENT2_AUDIT.md)，实验报告初稿见 [REPORT.md](REPORT.md)。

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
| 判题执行 | subprocess + CPU/文件大小/进程数限制 + psutil 内存监控 + 墙钟超时 | 超时和取消时清理进程组；这是本机资源控制，不提供容器级文件和网络隔离 |

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
│   ├── routing.py       #   在 JSON 解析前完成身份和管理员权限检查
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
    └── runner.py        # 判题引擎：进程执行/资源限制/输出比对（Step 2） ✅

data/problems/           # 题目配置文件（1002 / 1001 示例）
app.py                   # Streamlit 前端（题目/评测/用户管理/访问审计/AI 命题页面）
tests/                   # pytest 测试（含真实 Python/C++ 判题、AI 模拟接口、Streamlit 交互与文档符合性回归）
```

## 分层约定（扩展方式）

```
routers  →  HTTP 语义（路径/参数/状态码），只做转发
services →  业务逻辑（校验/存储/统计/编排）
models   →  数据结构（ORM / 文件）
```

**新增功能的标准做法**：在对应域下写 service → 写 router → 在 `main.py` 挂载
→ Streamlit 前端在 `app.py` 加页面函数，并通过 `st.Page` / `st.navigation` 注册。

## 评测流水线（Step 2/3）

提交后立即返回 `pending`，后台 `asyncio.create_task` 执行评测：

1. 编译（有 `compile_cmd` 的语言）失败 → `CE`；
2. 逐测试点执行：题目显式限制 → 语言限制 → 系统默认（3 秒 / 128 MB）；
   `RLIMIT_CPU` 与墙钟超时（超时→TLE）+ 内存监控（超限→MLE）+ 非零退出→RE，
   输出逐行比对（忽略行尾空白与末尾空行）→ AC/WA；
3. 每个测试点 10 分，`score = AC 数 × 10`。

内置语言为 Python 3 和 C++14。所有登录用户都可在前端“语言管理”页面查询、注册语言；后端对应
`GET /api/languages/` 和 `POST /api/languages/`。语言配置保存在数据库中，提交时按 `language` 字段
读取 `file_ext`、`compile_cmd`、`run_cmd` 及默认资源限制，因此新增配置可以立即参与评测。

`success` 表示评测正常完成，包括得到 WA/TLE/MLE/RE 的提交；`error` 用于编译失败或评测系统错误。
每题全部测试点 AC 才计入用户 `resolve_count`。C++ 编译成功和失败均返回结构化 `compile_info`。

重判先终止旧进程并清空旧分数和明细，再调度新任务；服务重启会修正旧版状态标记并重判遗留 pending 提交。
重置接口会停止后台评测和 AI 任务，清除会话、题目、记录、限流与 AI 配置，再恢复初始管理员和默认语言。

前端使用 `st.fragment` 每 1.5 秒查询状态，不整页刷新。登录成功后会先回读确认不透明会话 Cookie 已写入浏览器，整页刷新后经
`GET /api/auth/me` 校验并恢复身份。页面导航由 `st.navigation` / `st.switch_page` 统一管理，
前进、后退和页内返回均保留当前会话；
旧版 URL 中的 `oj_s` / `oj_u` 凭据会被移除。后端暂时不可达时保留 Cookie 和当前 URL，
只有明确登出、会话失效或账号被禁用时才清除。

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
| `POST /api/ai/problem-tasks/{id}/retry` | 从失败记录创建新任务，保留原错误与用量 |

设计要点：

- **R1 交互衔接**：前端 AI 命题页提交需求 → 实时进度 → 预览生成的题目 →
  经已有的 `POST/PUT /api/problems/` 接口导入题库，AI 模块本身不直接写题库，与基础功能解耦；
- **R2 可配置**：provider_url/model/api_key 均通过接口配置（OpenAI 兼容 chat/completions 协议，
  不写死厂商）；api_key 用 Fernet 加密存 `data/ai_config.json`（密钥文件 600 权限），
  任何接口/日志/错误信息都不泄露密钥；
- **R3 进度与中断**：后端 SSE 按订阅者广播 state/progress/usage/final 事件（含心跳）；前端使用 1.5s 片段轮询；
  模型调用期间每 2s 推送一次进度（progress 缓慢爬升 + "模型推理中（已 Xs）"），
  执行期间界面持续展示可观察的进度信息，而非等任务完成才返回结果；
  cancel 用 `task.cancel()` 真正终止后台任务，取消后不会被旧任务覆盖状态，
  并立即向所有 SSE 观察者推送 `final(status=cancelled)` 终态，界面明确展示已中断；
  模型请求由单一总时限管理，默认 600 秒，可用 `OJ_AI_REQUEST_TIMEOUT` 环境变量调整；
- **R4 用量计费**：`费用 = 输入Token/计价单位 × 输入单价 + 输出Token/计价单位 × 输出单价`；
  单价来源按优先级自动确定：① 模型接口在 `usage.cost` 直接返回的费用
  ② 模型配置中用户填写的输入/输出价格（前端提示：不同模型、不同时段的计费价格可能不同，
  部分厂商设有错峰优惠时段，请按实际调用时段的官方价格填写）
  ③ 未填价格且接口未返回费用时 `cost=null` 并在页面标注（`price_source: unknown`）；
  模型接口不返回完整用量时，对缺失项按字符数/4 估算（包含系统提示词），`usage.estimated=true` 并在页面标注；
- **校验入库**：模型输出必须通过 `ProblemConfig` 校验（非法 JSON/缺字段 → 任务 failed，
  错误信息脱敏）；服务重启时遗留 pending/running 任务自动标记 failed。

- AI 模型 URL 可填写完整 chat/completions 地址，或域名 / `/v1` 基地址（自动补全端点）；不接受把密钥放进 URL。
- 模型网络默认直连，避免遗留的 `HTTP_PROXY` / `HTTPS_PROXY` 导致连接失败；部署确实依赖系统代理时设置 `OJ_AI_TRUST_ENV=1` 并重启。
- 模型配置增加 `currency`（默认 CNY，可选 USD）；每个任务固定创建时的 URL、模型、密钥和价格。
- 失败任务可从详情页重新开始；新任务使用当前最新配置，原失败记录和已发生费用不会被覆盖。
- 重试累计每次调用的 Token 与费用，`usage.calls` 保留明细；模型返回后即保存用量，题目校验失败也不会丢失账单信息。
- 返回前被中断或网络失败的调用可能没有完整用量，页面显示未知，不把未知当作零费用。
- 生成题目提供 JSON 审阅编辑及实际测试点输入输出预览；改编任务保持原题 id，日志公开策略由管理员维护。
- 自动测试不调用付费模型。真实题目的合理性与测试规模区分能力仍需按验收需求用实际模型检查。

## 题库数据验证

`tests/test_demo_problems.py` 独立检查示例题目的输入范围及标准输出，
并验证 n=200000、q=50000 时 Python/C++14 二分解法通过、Python 线性扫描超时。

生成可导入的性能测试配置（输出到 Git 忽略目录，不覆盖现有题目）：

```bash
.venv/bin/python scripts/build_1003_stress.py
# 最大规模：增加 --n 1000000 --q 100000
```

验证记录：完整回归 275 项通过（其中包含 6 项题库验证）。
