// 登录/注册视图与顶栏用户状态
function renderUserBox() {
  const box = document.getElementById("user-box");
  if (currentUser) {
    box.innerHTML = `
      <a href="#/user">${escapeHtml(currentUser.username)}</a>
      ${isAdmin() ? '<span class="badge">admin</span>' : ""}
      <button id="logout-btn" class="link-btn">退出</button>`;
    document.getElementById("logout-btn").onclick = async () => {
      try { await api.post("/api/auth/logout"); } catch (e) { /* 已过期也无妨 */ }
      setCurrentUser(null);
      renderUserBox();
      toast("已退出");
      location.hash = "#/problems";
    };
  } else {
    box.innerHTML = `<a href="#/login">登录</a>`;
  }
}

Views.login = () => {
  document.getElementById("app").innerHTML = `
    <div class="card form-card">
      <h2>登录</h2>
      <form id="login-form">
        <label>用户名<input name="username" required></label>
        <label>密码<input name="password" type="password" required></label>
        <button type="submit" class="btn">登录</button>
      </form>
      <p class="muted">还没有账号？<a href="#/register">注册</a></p>
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
    <div class="card form-card">
      <h2>注册</h2>
      <form id="register-form">
        <label>用户名<input name="username" required></label>
        <label>密码<input name="password" type="password" required></label>
        <button type="submit" class="btn">注册</button>
      </form>
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
