// Step 3 评测页面（占位）。
// 后续实现：提交表单 → POST /api/submissions/；
// 记录列表/详情 → GET /api/submissions/({id})；重新评测 → PUT .../rejudge
Views.submissionList = () => {
  document.getElementById("app").innerHTML = `
    <div class="card">
      <h2>评测提交</h2>
      <p class="muted">Step 3（评测管理）尚未实现：提交记录查询、状态管理与重新评测。</p>
    </div>`;
};
