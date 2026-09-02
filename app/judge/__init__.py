"""判题引擎（Step 2/3，待实现）。

架构设计（见 runner.py 顶部注释）：
- 沙箱执行 + 资源限制（时间/内存/输出量）
- 输出比对（逐行去尾部空白，预留 Special Judge 扩展点）
- 语言注册表驱动（编译/运行命令模板来自 languages 表）

评测流程（Step 3 提交后触发）：
    提交写入 Submission(pending)
    → 后台任务（asyncio.create_task）执行评测
    → 逐测试点运行并记录 TestcaseResult
    → 汇总 status / score / counts 写回 Submission
"""
