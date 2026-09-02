// 前端路由（hash 模式）：解析 #/problems/xxx 并分发到对应视图
// 新增页面时：在 views/ 下写视图函数 + 在此注册路由即可
const routes = [
  { pattern: /^#\/problems\/new\/?$/, view: "problemEdit" },
  { pattern: /^#\/problems\/([^/]+)\/edit\/?$/, view: "problemEdit" },
  { pattern: /^#\/problems\/([^/]+)\/?$/, view: "problemDetail" },
  { pattern: /^#\/problems\/?$/, view: "problemList" },
  { pattern: /^#\/submissions\/([^/]+)\/?$/, view: "submissionDetail" },
  { pattern: /^#\/submissions\/?$/, view: "submissionList" },
  { pattern: /^#\/ai\/tasks\/([^/]+)\/?$/, view: "aiTaskDetail" },
  { pattern: /^#\/ai\/?$/, view: "aiHome" },
  { pattern: /^#\/admin\/users\/?$/, view: "adminUsers" },
  { pattern: /^#\/login\/?$/, view: "login" },
  { pattern: /^#\/register\/?$/, view: "register" },
  { pattern: /^#\/user\/?$/, view: "userHome" },
];

// 解析 hash 中的查询参数（如 #/submissions?problem_id=xxx）
function hashParams() {
  const q = (location.hash || "").split("?")[1] || "";
  return new URLSearchParams(q);
}

// 导航高亮：根据当前路由给对应 nav 链接加 active；管理员专属链接按角色显示
function renderNav() {
  const path = (location.hash || "#/problems").split("?")[0];
  // 登录/注册页本身就是认证界面，隐藏顶栏重复的「登录」按钮（已登录用户的头像区保留）
  const onAuthPage = path === "#/login" || path === "#/register";
  document.getElementById("user-box").hidden = !currentUser && onAuthPage;
  document.querySelectorAll(".nav-links a").forEach((a) => {
    const key = a.dataset.nav;
    if (key === "admin") {
      a.hidden = !isAdmin();
      return;
    }
    a.classList.toggle("active",
      key === "ai" ? path.startsWith("#/ai") : path.startsWith(`#/${key}`));
  });
}

async function render() {
  const path = (location.hash || "#/problems").split("?")[0];
  renderNav();
  for (const r of routes) {
    const m = path.match(r.pattern);
    if (m) return Views[r.view](...m.slice(1));
  }
  Views.notFound();
}

window.addEventListener("hashchange", render);
renderUserBox();
render();
