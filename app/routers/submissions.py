"""Step 3 评测管理接口（待实现）。

接口规格（api.md）：
- POST /api/submissions/                        登录用户提交：problem_id, language, code 必填
    → {"submission_id": ..., "status": "pending"}
    异常：400（参数）/401/403（题目或语言不存在 404）/429（1 分钟内超 3 次）
- GET  /api/submissions/                        列表（本人或管理员）
    一级条件：user_id, problem_id（不可全空）；二级条件：status, page, page_size
    → {total, submissions[]}；error/pending 记录只返回 id 和 status
- GET  /api/submissions/{submission_id}         详情（本人或管理员）
    → submission_id, status, score, counts, compile_info, run_info, error_info
    error_info 不得泄露敏感路径/密钥；pending 时未产生的字段为 null
- PUT  /api/submissions/{submission_id}/rejudge 重新评测（仅管理员，覆盖原记录）

实现提示：
- 提交后写入 Submission(status=pending)，用 asyncio.create_task 启动
  app/judge 的评测（异步评测是本实验重点，勿在请求内同步等待）；
- 限流使用 core/rate_limit.RateLimiter(config.SUBMIT_RATE_LIMIT, config.SUBMIT_RATE_WINDOW)，
  按用户 id 计数；
- 完成后在 main.py 取消 include_router 注释。

router = APIRouter(prefix="/api/submissions", tags=["submissions"])
"""
