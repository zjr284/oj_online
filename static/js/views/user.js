// 用户视图：我的信息 + 管理员用户管理（Step 4 + Step 6）
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
        <div class="actions"><a href="#/submissions" class="btn">我的提交</a></div>
      </div>`;
  } catch (err) {
    if (err.code === 401) { setCurrentUser(null); renderUserBox(); location.hash = "#/login"; return; }
    app.innerHTML = `<div class="card">加载失败：${escapeHtml(err.message)}</div>`;
  }
};

// 管理员：用户列表 + 角色管理 + 创建管理员（Step 4 权限管理）
Views.adminUsers = async () => {
  const app = document.getElementById("app");
  if (!isAdmin()) {
    location.hash = "#/login";
    return;
  }
  app.innerHTML = `<div class="card"><h2>用户管理</h2><p class="muted">加载中…</p></div>`;

  let data;
  try {
    data = await api.get("/api/users/");
  } catch (err) {
    if (err.code === 401) { setCurrentUser(null); renderUserBox(); location.hash = "#/login"; return; }
    app.innerHTML = `<div class="card">加载失败：${escapeHtml(err.message)}</div>`;
    return;
  }

  const rows = data.users
    .map((u) => `
      <tr>
        <td>${u.user_id}</td>
        <td>${escapeHtml(u.username)}</td>
        <td>
          <select data-uid="${u.user_id}" data-orig="${escapeHtml(u.role)}" class="role-select">
            ${["user", "admin", "banned"].map((r) => `<option value="${r}" ${r === u.role ? "selected" : ""}>${r}</option>`).join("")}
          </select>
        </td>
        <td>${escapeHtml(u.join_time)}</td>
        <td>${u.submit_count}</td>
        <td>${u.resolve_count}</td>
      </tr>`)
    .join("");

  app.innerHTML = `
    <div class="card">
      <h2>用户管理 <span class="muted">共 ${data.total} 人</span></h2>
      <table class="table">
        <thead><tr><th>ID</th><th>用户名</th><th>角色</th><th>注册时间</th><th>提交数</th><th>通过题数</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div class="card">
      <h2>创建管理员</h2>
      <form id="create-admin-form">
        <label>用户名（3–40 字符）<input name="username" required minlength="3" maxlength="40"></label>
        <label>密码（至少 6 位）<input name="password" type="password" required minlength="6"></label>
        <button type="submit" class="btn">创建</button>
      </form>
    </div>`;

  document.querySelectorAll(".role-select").forEach((sel) => {
    sel.onchange = async () => {
      const uid = sel.dataset.uid;
      const role = sel.value;
      if (role === sel.dataset.orig) return;
      if (!confirm(`确认将用户 ${uid} 的角色改为 ${role}？`)) {
        sel.value = sel.dataset.orig;
        return;
      }
      try {
        await api.put(`/api/users/${uid}/role`, { role });
        sel.dataset.orig = role;
        toast("已更新");
      } catch (err) {
        toast(err.message, false);
        sel.value = sel.dataset.orig;
      }
    };
  });

  document.getElementById("create-admin-form").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target;
    try {
      await api.post("/api/users/admin", { username: f.username.value, password: f.password.value });
      toast("已创建管理员");
      Views.adminUsers();
    } catch (err) { toast(err.message, false); }
  };
};

Views.notFound = () => {
  document.getElementById("app").innerHTML = `
    <div class="card"><h2>404</h2><p class="muted">页面不存在。<a href="#/problems">返回题目列表</a></p></div>`;
};
