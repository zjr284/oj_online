// 用户视图：我的信息 + 管理员用户管理（Step 4 + Step 6）

// 角色徽章：user 灰 / admin 蓝 / banned 红（配色对应 style.css 的 .badge.role-user / .badge.admin / .badge.role-banned）
function roleBadge(role) {
  const map = { user: ["role-user", "用户"], admin: ["admin", "管理员"], banned: ["role-banned", "封禁"] };
  const [cls, label] = map[role] || ["role-user", String(role || "user")];
  return `<span class="badge ${cls}">${escapeHtml(label)}</span>`;
}

Views.userHome = async () => {
  const app = document.getElementById("app");
  if (!currentUser) {
    location.hash = "#/login";
    return;
  }
  app.innerHTML = `<div class="card empty"><div class="icon">⏳</div><p>加载中…</p></div>`;
  try {
    const u = await api.get(`/api/users/${currentUser.user_id}`);
    // 统计：通过率 = 通过题数 / 提交数，保留 1 位小数（无提交时显示 —）
    const sc = Number(u.submit_count) || 0;
    const rc = Number(u.resolve_count) || 0;
    const rate = sc > 0 ? `${((rc / sc) * 100).toFixed(1)}%` : "—";
    const initial = String(u.username || "?").charAt(0).toUpperCase();
    app.innerHTML = `
      <div class="page-head"><h1>个人主页</h1></div>
      <div class="card profile-card">
        <span class="avatar lg">${escapeHtml(initial)}</span>
        <div>
          <h2>${escapeHtml(u.username)} ${roleBadge(u.role)}</h2>
          <p class="muted">注册时间：${escapeHtml(u.join_time)}</p>
        </div>
      </div>
      <div class="stat-grid">
        <div class="stat-card"><div class="num">${sc}</div><div class="lbl">提交数</div></div>
        <div class="stat-card"><div class="num success">${rc}</div><div class="lbl">通过题数</div></div>
        <div class="stat-card"><div class="num primary">${rate}</div><div class="lbl">通过率</div></div>
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
  app.innerHTML = `<div class="card empty"><div class="icon">⏳</div><p>加载中…</p></div>`;

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
          ${roleBadge(u.role)}
          <select data-uid="${u.user_id}" data-orig="${escapeHtml(u.role)}" class="role-select">
            ${["user", "admin", "banned"].map((r) => `<option value="${r}" ${r === u.role ? "selected" : ""}>${r}</option>`).join("")}
          </select>
        </td>
        <td>${escapeHtml(u.join_time)}</td>
        <td class="num">${u.submit_count}</td>
        <td class="num">${u.resolve_count}</td>
      </tr>`)
    .join("");

  app.innerHTML = `
    <div class="page-head"><h1>用户管理</h1></div>
    <div class="card">
      <h2>用户列表 <span class="muted">共 ${data.total} 人</span></h2>
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

// 404 空状态：图标 + 提示 + 返回题目列表
Views.notFound = () => {
  document.getElementById("app").innerHTML = `
    <div class="card empty">
      <div class="icon">🧭</div>
      <p>页面不存在</p>
      <p><a href="#/problems" class="btn">返回题目列表</a></p>
    </div>`;
};
