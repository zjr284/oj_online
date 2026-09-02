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

已实现：Step 1 题目管理（含 log_visibility）、Step 4 用户核心、语言注册表、/api/reset/、前端题目/登录页面。
待实现（骨架文件含 api.md 规格注释）：Step 2 `app/judge/runner.py`、Step 3 `app/routers/submissions.py`、Step 5 `app/routers/logs.py`、Advance `app/routers/ai.py`。
