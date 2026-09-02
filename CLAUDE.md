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
新增模块：service → router → `main.py` 挂载 → 前端视图（`static/js/views/`，路由注册在 `static/js/app.js`）。

## 状态

已实现：Step 1–6 + Advance AI 命题全部完成——题目管理（含 log_visibility）、评测引擎（沙箱/CE/TLE/MLE/RE/AC/WA）、
评测管理（异步编排/重判代际/重启恢复/限流 429）、用户管理（bcrypt/操作日志/角色权限）、
评测日志（明细可见性 + 访问审计）、Step 6 前端有两套：Streamlit（app.py，step6.md 要求，用户/题目/评测三组页面，
Cookie 经 httpx 传递、表单提交前格式检查、提交后轮询）、Web 前端（提交面板/评测列表详情轮询/用户管理）、
AI 命题（model-config/problem-tasks/SSE 进度/取消/用量计费，api_key Fernet 加密永不返回；
R3：模型调用期间 ticker 每 2s 推进度、cancel 推 final(cancelled) 即时通知观察者；
费用 = 用户填写价格或接口返回 usage.cost，未填且接口未返回则 cost=null 标注；
前端提示不同模型不同时段价格可能不同）。

实现细节备忘：
- 题目/语言不存在 → 404（api.md 语义）；语言注册权限为**任意登录用户**（Step 2/4 要求）。
- 提交限流 `config.SUBMIT_RATE_LIMIT`（环境变量 `OJ_SUBMIT_RATE_LIMIT`），测试用 monkeypatch 收紧。
- log 接口：details 仅管理员/公开题目可见；本人看未公开题目省略 details；
  403（已登录无权）与 200 都记 AccessLog；提交不存在返回 404 且不记审计。
- 提交列表 error/pending 条目只返回 {submission_id, status}（api.md）；submission_id、user_id 均为字符串。
- api.md 字段语义（2026-09 审计后对齐）：`counts` = 本题总分数（测试点数目×10，DB 列 total_score）；
  各结果统计经 extra 字段 `verdicts` 返回（DB 列 counts 存 dict）；
  `compile_info`/`run_info` 为 {"result", "message"} 对象（DB 存 JSON 字符串）；
  成功 msg 默认 "success"，各接口特定 msg 见 api.md 示例（add success / login success 等）。
- 安全校验：题目 id 限 `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`（防经 body 的路径穿越）、
  必填字段非空、samples/testcases 非空、time/memory 限制为正；语言 name/file_ext 限安全字符集。
- 评测沙箱：运行与编译阶段均限 RLIMIT_CPU/RLIMIT_FSIZE/RLIMIT_NPROC(4096)；
  NPROC 取 4096 是因为 Linux 按 UID 全系统线程数计数（VSCode 等占数百），过低会让 g++ vfork EAGAIN。
- reset 会重建初始管理员**并恢复默认语言**（python/cpp）。
