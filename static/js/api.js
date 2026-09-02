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

// ---------- 共享视图辅助（设计系统契约，见 style.css） ----------

// 难度徽章：LeetCode 配色（简单青绿 / 中等金黄 / 困难红）
function diffClass(difficulty) {
  const s = String(difficulty || "").toLowerCase();
  if (/(简|易|入门|easy)/.test(s)) return "diff-easy";
  if (/(难|困|hard)/.test(s)) return "diff-hard";
  return "diff-medium";
}
function diffChip(difficulty) {
  if (!difficulty) return "";
  return `<span class="diff-chip ${diffClass(difficulty)}">${escapeHtml(difficulty)}</span>`;
}

// 评测结果徽章/彩条（单测试点结果）
const VERDICT_TEXT = {
  AC: "通过", WA: "答案错误", TLE: "超出时间限制", MLE: "超出内存限制",
  RE: "运行时错误", CE: "编译错误", UNK: "未知",
};
const verdictClass = (r) => String(r || "unk").toLowerCase();
function verdictPill(result, count) {
  const r = String(result || "UNK").toUpperCase();
  const cls = verdictClass(r);
  return `<span class="verdict-case ${cls}"><b>${escapeHtml(r)}</b>${VERDICT_TEXT[r] || ""}${count != null ? ` × ${count}` : ""}</span>`;
}

// 复制按钮绑定：<button class="copy-btn" data-copy="...">复制</button>
function bindCopyButtons(root = document) {
  root.querySelectorAll(".copy-btn").forEach((btn) => {
    btn.onclick = async () => {
      const text = btn.dataset.copy || "";
      try {
        await navigator.clipboard.writeText(text);
        toast("已复制到剪贴板");
      } catch {
        toast("复制失败，请手动选择复制", false);
      }
    };
  });
}

// 未登录（或会话过期）时的统一提示卡片
function unauthHtml(msg = "该页面需要登录后查看") {
  return `
    <div class="card empty">
      <div class="icon">🔒</div>
      <p>${msg}</p>
      <p><a href="#/login" class="btn">去登录</a></p>
    </div>`;
}

function toast(msg, ok = true) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.className = ok ? "show" : "show error";
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (el.className = ""), 2500);
}
