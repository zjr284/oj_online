// 前端路由（hash 模式）：解析 #/problems/xxx 并分发到对应视图
// 新增页面时：在 views/ 下写视图函数 + 在此注册路由即可
const routes = [
  { pattern: /^#\/problems\/new\/?$/, view: "problemEdit" },
  { pattern: /^#\/problems\/([^/]+)\/edit\/?$/, view: "problemEdit" },
  { pattern: /^#\/problems\/([^/]+)\/?$/, view: "problemDetail" },
  { pattern: /^#\/problems\/?$/, view: "problemList" },
  { pattern: /^#\/submissions\/?$/, view: "submissionList" },
  { pattern: /^#\/login\/?$/, view: "login" },
  { pattern: /^#\/register\/?$/, view: "register" },
  { pattern: /^#\/user\/?$/, view: "userHome" },
];

async function render() {
  const hash = location.hash || "#/problems";
  for (const r of routes) {
    const m = hash.match(r.pattern);
    if (m) return Views[r.view](...m.slice(1));
  }
  Views.notFound();
}

window.addEventListener("hashchange", render);
renderUserBox();
render();
