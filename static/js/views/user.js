// 用户视图：我的信息。
// Step 4 完整实现时补充：管理员视角的用户列表 / 创建管理员 / 角色调整。
Views.userHome = async () => {
  const app = document.getElementById("app");
  if (!currentUser) {
    location.hash = "#/login";
    return;
  }
  app.innerHTML = `<div class="card">加载中…</div>`;
  try {
    const u = await api.get(`/api/users/${currentUser.user_id}`);
    app.innerHTML = `
      <div class="card">
        <h2>我的信息</h2>
        <table class="table">
          <tr><td>用户名</td><td>${escapeHtml(u.username)}</td></tr>
          <tr><td>角色</td><td>${escapeHtml(u.role)}</td></tr>
          <tr><td>注册时间</td><td>${escapeHtml(u.join_time)}</td></tr>
          <tr><td>提交数</td><td>${u.submit_count}</td></tr>
          <tr><td>通过题目数</td><td>${u.resolve_count}</td></tr>
        </table>
      </div>`;
  } catch (err) {
    app.innerHTML = `<div class="card">加载失败：${escapeHtml(err.message)}</div>`;
  }
};

Views.notFound = () => {
  document.getElementById("app").innerHTML = `
    <div class="card"><h2>404</h2><p class="muted">页面不存在。<a href="#/problems">返回题目列表</a></p></div>`;
};
