"""Step 5 评测日志接口（待实现）。

接口规格（api.md）：
- GET /api/submissions/{submission_id}/log   测试点明细（本人或管理员）
    → {details: [{id, result, time, memory}], score, counts}
    管理员始终可见；普通用户仅在题目 public_cases=True 时可见 details，
    否则省略该字段（且需要记录访问审计）。
- GET /api/logs/access/                      访问审计列表（仅管理员）
    筛选：user_id, problem_id, page, page_size；action 仅为 view_logs，
    status 记录访问结果（允许/拒绝）。
    不记录：未登录、评测不存在、参数错误的访问。

实现提示：
- 明细数据来自 testcase_results 表（见 models/submission.py）；
- 每次访问日志接口都写 access_logs 表（含被拒绝的访问）；
- 路由可以定义在本文件或 submissions.py 中（前缀不同，建议本文件
  只放 /api/logs/access/，submission 明细放 submissions.py）；
- 完成后在 main.py 取消 include_router 注释。

router = APIRouter(prefix="/api/logs", tags=["logs"])
"""
