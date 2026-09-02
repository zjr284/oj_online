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
.venv/bin/uvicorn app.main:app --reload      # 启动（或 ./run.sh）
.venv/bin/pytest -q                          # 测试（用独立临时数据目录）
curl -c jar -X POST localhost:8000/api/auth/login -H 'Content-Type: application/json' -d '{"username":"admin","password":"admintestpassword"}'
```

## 分层

routers（HTTP 语义）→ services（业务逻辑）→ models / ProblemStore（存储）。
新增模块：service → router → `main.py` 挂载 → 前端视图（`static/js/views/`，路由注册在 `static/js/app.js`）。

## 状态

已实现：Step 1–6 全部完成——题目管理（含 log_visibility）、评测引擎（沙箱/CE/TLE/MLE/RE/AC/WA）、
评测管理（异步编排/重判代际/重启恢复/限流 429）、用户管理（bcrypt/操作日志/角色权限）、
评测日志（明细可见性 + 访问审计）、前端页面（提交面板/评测列表详情轮询/用户管理）。
待实现（骨架文件含 api.md 规格注释）：Advance `app/routers/ai.py`。

实现细节备忘：
- 题目/语言不存在 → 404（api.md 语义）；语言注册权限为**任意登录用户**（Step 2/4 要求）。
- 提交限流 `config.SUBMIT_RATE_LIMIT`（环境变量 `OJ_SUBMIT_RATE_LIMIT`），测试用 monkeypatch 收紧。
- log 接口：details 仅管理员/公开题目可见；本人看未公开题目省略 details；
  403（已登录无权）与 200 都记 AccessLog；提交不存在返回 404 且不记审计。
- 提交列表 error/pending 条目只返回 {submission_id, status}（api.md）；submission_id 为字符串。
