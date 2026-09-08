# CLAUDE.md

## 项目概况

清华 Python 课程「实验二：在线评测系统」。FastAPI 异步 OJ，
验收时间 2026-09-10。实验权威文档：https://dbg-course.github.io/python-docs/oj/ （api.md 是接口的唯一标准）。

## 硬性约束

- **所有接口必须 `async def`**（FastAPI 异步接口），否则实验 0 分。
- 响应格式统一 `{"code": <HTTP 状态码>, "msg": str, "data": ...}`；
  异常顺序 401 > 403 > 400 > 429 > 409 > 404 > 500；参数校验错误返回 400（非 422）。
- 权限判断全部在后端（`app/core/deps.py` 的 `get_current_user` / `require_admin`）。
- 题目以 JSON 文件存 `data/problems/`（每题一个），业务代码经 `ProblemStore` 访问。
- Git 提交遵循 Conventional Commits；大文件不入库。

## 常用命令

```bash
./run.sh                                     # 一键启动：后端(8000)+Streamlit(8501)，已运行则跳过，Ctrl+C 停
.venv/bin/uvicorn app.main:app --reload      # 仅启动后端（调试用）
.venv/bin/streamlit run app.py               # Step 6 前端（需后端已启动；OJ_API_BASE 可改后端地址）
.venv/bin/pytest -q                          # 测试（用独立临时数据目录）
curl -c jar -X POST localhost:8000/api/auth/login -H 'Content-Type: application/json' -d '{"username":"admin","password":"admintestpassword"}'
```

## 分层

routers（HTTP 语义）→ services（业务逻辑）→ models / ProblemStore（存储）。
新增模块：service → router → `main.py` 挂载 → Streamlit 页面（app.py：`st.Page` 注册 + `st.navigation` 分发）。

## 状态

已实现：Step 1–6 + Advance AI 命题主要功能（符合性核对见 EXPERIMENT2_AUDIT.md）——题目管理（含 log_visibility）、评测引擎（资源控制/CE/TLE/MLE/RE/AC/WA）、
评测管理（异步编排/重判代际/重启恢复/限流 429）、用户管理（bcrypt/操作日志/角色权限）、
评测日志（明细可见性 + 访问审计）、Step 6 前端：Streamlit（app.py，step6.md 要求，题目/评测/用户管理/
访问审计/AI 命题页面，Cookie 经 httpx 传递、表单提交前格式检查、提交后轮询；
洛谷×力扣主题：.streamlit/config.toml 配色 + app.py `_UI_CSS` 全局样式与 `_badge`/`_html_table` 徽章表格、
装饰层：stApp 浅蓝渐变底 + 圆点网格 + 角落光晕（`background-attachment: fixed`）、侧边栏渐变底 + 顶部彩带 +
品牌短横线、`_HERO` 横幅装饰圆、指标卡/oj-card 悬浮抬升）、
AI 命题（model-config/problem-tasks/SSE 进度/取消/失败重新开始/用量计费，api_key Fernet 加密永不返回；
R3：模型调用期间 ticker 每 2s 推进度、cancel 推 final(cancelled) 即时通知观察者；
费用 = 用户填写价格或接口返回 usage.cost，未填且接口未返回则 cost=null 标注；
前端提示不同模型不同时段价格可能不同）。

实现细节备忘：
- 题目/语言不存在 → 404（api.md 语义）；登录用户可创建题目、注册语言和使用 AI 命题，删除题目及修改日志公开策略仅管理员。
- 提交限流 `config.SUBMIT_RATE_LIMIT`（环境变量 `OJ_SUBMIT_RATE_LIMIT`），测试用 monkeypatch 收紧。
- log 接口：details 仅管理员/公开题目可见；本人看未公开题目省略 details；
  403（已登录无权）与 200 都记 AccessLog；提交不存在返回 404 且不记审计。
- GET /api/logs/access/ 返回含 log_id/username 的纯数组（无 total），保留 user_id 兼容；
  Streamlit 审计页按用户名展示和筛选，每行末尾有详情/删除操作，当前页满时用同页长探测下一页；
  DELETE /api/logs/access/{log_id} 仅管理员可用。
- 题目详情页力扣式双栏：左侧题面 tabs，右侧内嵌提交面板（语言/代码按题目 id 缓存 widget key），
  提交后就地轮询结果；独立「提交评测」导航页已移除，代码提交统一走题目详情内嵌面板。
- Streamlit AppTest 冒烟测试用 monkeypatch 把 OJ_API_BASE 指向死端口隔离真实后端，
  否则页面真实请求 live 后端 401 会触发 _clear_session 清空预置 me。
- 顶层导航统一使用 `st.navigation` / `st.page_link`，页内跳转使用 `st.switch_page`；
  禁止用 HTML 链接、`st.link_button` 或自定义 popstate/reload 实现内部导航。
  透明 stHeader 需加 `pointer-events: none`，避免不可见的固定头栏拦截顶部区域点击。
- 登录 Cookie 禁止写入 URL；旧版 oj_s/oj_u 会被移除。浏览器 Cookie 仅用于
  整页刷新后恢复登录，写入后回读确认，不参与导航；暂时连接失败不删 Cookie 或改变当前 URL。
  评测和 AI 进度使用 st.fragment 的 1.5 秒片段轮询；AI 模型总超时默认 600 秒，可用 OJ_AI_REQUEST_TIMEOUT 覆盖；
  模型请求默认不继承系统代理，需要时用 OJ_AI_TRUST_ENV=1 开启。
- success 表示评测正常返回结果，WA/TLE/MLE/RE 均属于 success；全部 AC 才增加 resolve_count。
  error 用于 CE 或评测系统错误。题目显式限制优先于语言限制，再回退至系统默认。
- 题目编辑省略 public_cases 时保留原策略；普通用户不能经题目增改接口更改公开策略。
- AuthenticatedRoute 在解析请求体之前认证，确保畸形 JSON/非法路径也遵循 401 > 403 > 400。
- 提交列表 error/pending 条目只返回 {submission_id, status}（api.md）；submission_id、user_id 均为字符串。
- api.md 字段语义（2026-09 审计后对齐）：`counts` = 本题总分数（测试点数目×10，DB 列 total_score）；
  各结果统计经 extra 字段 `verdicts` 返回（DB 列 counts 存 dict）；
  `compile_info`/`run_info` 为 {"result", "message"} 对象（DB 存 JSON 字符串）；
  成功 msg 默认 "success"，各接口特定 msg 见 api.md 示例（add success / login success 等）；
  用户列表按 submit_count 降序（并列按 user_id 升序，翻页稳定），与 api.md 示例一致。
- 安全校验：题目 id 限 `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`（防经 body 的路径穿越）、
  必填字段非空、samples/testcases 非空、time/memory 限制为正；语言 name/file_ext 限安全字符集。
- 本机评测资源控制（不提供容器级隔离）：运行与编译阶段均限 RLIMIT_CPU/RLIMIT_FSIZE/RLIMIT_NPROC(4096)；
  NPROC 取 4096 是因为 Linux 按 UID 全系统线程数计数（VSCode 等占数百），过低会让 g++ vfork EAGAIN。
- reset 先停止评测和 AI 任务，清空限流与模型配置，再重建初始管理员和默认语言。

- AI SSE 每个订阅者使用独立队列，避免分走终态事件；配置在创建任务时固定。重试累计用量，校验失败保留用量。
