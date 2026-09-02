// 统一 API 客户端：对接后端 {code, msg, data} 响应协议
// 各视图函数统一挂载到 Views 命名空间（api.js 最先加载，在此声明）
const Views = {};

const api = {
  async request(method, path, body) {
    let resp;
    try {
      resp = await fetch(path, {
        method,
        headers: body !== undefined ? { "Content-Type": "application/json" } : {},
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
    } catch (e) {
      const err = new Error("网络错误");
      err.code = 0;
      throw err;
    }
    let data = null;
    try { data = await resp.json(); } catch (e) { /* 非 JSON 响应 */ }
    if (!data || data.code !== 200) {
      const err = new Error((data && data.msg) || `HTTP ${resp.status}`);
      err.code = data ? data.code : resp.status;
      throw err;
    }
    return data.data;
  },
  get: (p) => api.request("GET", p),
  post: (p, b) => api.request("POST", p, b),
  put: (p, b) => api.request("PUT", p, b),
  del: (p) => api.request("DELETE", p),
};

// 当前登录用户（仅用于界面展示；权限判断全部在后端完成）
let currentUser = JSON.parse(localStorage.getItem("oj_user") || "null");

function setCurrentUser(u) {
  currentUser = u;
  localStorage.setItem("oj_user", u ? JSON.stringify(u) : "null");
}

function isAdmin() { return currentUser && currentUser.role === "admin"; }

// XSS 防护：所有用户可控内容渲染前转义
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function toast(msg, ok = true) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.className = ok ? "show" : "show error";
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (el.className = ""), 2500);
}
