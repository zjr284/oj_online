"""Advance 进阶模块：AI 智能命题接口（待实现）。

接口规格（api.md，允许等价设计但需在文档说明）：
- PUT  /api/ai/model-config        配置模型：provider_url, model, api_key 必填
    → 返回配置但不含 api_key，仅 {api_key_configured: true}
- POST /api/ai/problem-tasks/      创建命题任务：requirement 必填，problem_id 可选
    → {task_id, status: "pending"}；异常 400/401/404/500
- GET  /api/ai/problem-tasks/{task_id}         任务状态（创建者或管理员）
    → {task_id, status, progress, result, usage}
    status: pending/running/done/cancelled/failed
    usage: {input_tokens, output_tokens, total_tokens, cost, currency}
- GET  /api/ai/problem-tasks/{task_id}/events  进度事件（SSE/流式/轮询均可）
- PUT  /api/ai/problem-tasks/{task_id}/cancel  取消任务（须真正终止后台任务；已结束 409）

安全要求（api.md）：
- api_key 不得经查询接口或普通响应返回；存储需保护（如环境变量/加密）；
- 费用公式：输入 Token 数/计价单位 × 输入单价 + 输出 Token 数/计价单位 × 输出单价；
- 调用外部模型需处理超时/失败，避免任务长期占用资源；
- 校验模型返回数据后再入库。

实现提示：
- 任务模型已预留（models/ai.py: AiTask）；
- 后台任务用 asyncio.create_task 执行，cancel 接口用 task.cancel() 真正终止；
- 生成结果（题目配置）校验通过后可直接写入 ProblemStore；
- 完成后在 main.py 取消 include_router 注释。

router = APIRouter(prefix="/api/ai", tags=["ai"])
"""
