// 登录/注册视图与顶栏用户状态

// 登录/注册页左侧共用品牌面板（LeetCode 风格分栏，样式见 style.css 的 .auth-wrap / .auth-brand）
const AUTH_BRAND_HTML = `
    <div class="auth-brand">
      <h1>Online Judge</h1>
      <p>高性能异步在线评测系统：提交代码，即刻获得沙箱评测结果。</p>
      <ul>
        <li>⚡ 异步评测引擎</li>
        <li>🤖 AI 智能命题</li>
        <li>🛡️ 沙箱安全判题</li>
      </ul>
    </div>`;

// 顶栏用户区：登录后显示「头像 + 用户名 + 管理员徽章 + 退出」，未登录显示「登录」链接
function renderUserBox() {
  const box = document.getElementById("user-box");
  if (currentUser) {
    const initial = String(currentUser.username || "?").charAt(0).toUpperCase();
    box.innerHTML = `
      <a href="#/user" class="user-chip">
        <span class="avatar">${escapeHtml(initial)}</span>
        <span class="user-name">${escapeHtml(currentUser.username)}</span>
        ${isAdmin() ? '<span class="badge admin">管理员</span>' : ""}
      </a>
      <button id="logout-btn" class="link-btn">退出</button>`;
    document.getElementById("logout-btn").onclick = async () => {
      try { await api.post("/api/auth/logout"); } catch (e) { /* 已过期也无妨 */ }
      setCurrentUser(null);
      renderUserBox();
      toast("已退出");
      location.hash = "#/problems";
    };
  } else {
    box.innerHTML = `<a href="#/login" class="btn sm">登录</a>`;
  }
}

Views.login = () => {
  document.getElementById("app").innerHTML = `
    <div class="auth-wrap">
      ${AUTH_BRAND_HTML}
      <div class="auth-form">
        <h2>登录</h2>
        <form id="login-form">
          <label>用户名<input name="username" required></label>
          <label>密码<input name="password" type="password" required></label>
          <button type="submit" class="btn block">登录</button>
        </form>
        <p class="muted swap">还没有账号？<a href="#/register">注册</a></p>
      </div>
    </div>`;
  document.getElementById("login-form").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target;
    try {
      const u = await api.post("/api/auth/login", {
        username: f.username.value, password: f.password.value,
      });
      setCurrentUser(u);
      renderUserBox();
      toast("登录成功");
      location.hash = "#/problems";
    } catch (err) { toast(err.message, false); }
  };
};

Views.register = () => {
  document.getElementById("app").innerHTML = `
    <div class="auth-wrap">
      ${AUTH_BRAND_HTML}
      <div class="auth-form">
        <h2>注册</h2>
        <form id="register-form">
          <label>用户名<input name="username" required></label>
          <label>密码<input name="password" type="password" required></label>
          <button type="submit" class="btn block">注册</button>
        </form>
        <p class="muted swap">已有账号？<a href="#/login">登录</a></p>
      </div>
    </div>`;
  document.getElementById("register-form").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target;
    try {
      await api.post("/api/users/", { username: f.username.value, password: f.password.value });
      toast("注册成功，正在登录…");
      const u = await api.post("/api/auth/login", { username: f.username.value, password: f.password.value });
      setCurrentUser(u);
      renderUserBox();
      location.hash = "#/problems";
    } catch (err) { toast(err.message, false); }
  };
};
