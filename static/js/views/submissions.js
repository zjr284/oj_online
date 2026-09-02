// 评测视图：提交列表 / 提交详情（Step 3 + Step 6）
const STATUS_TEXT = { pending: "等待评测", success: "通过", error: "未通过" };

function statusBadge(status) {
  const cls = { pending: "pending", success: "ok", error: "fail" }[status] || "pending";
  return `<span class="badge ${cls}">${STATUS_TEXT[status] || status}</span>`;
}

// 列表：支持 ?problem_id=xxx&status=xxx 筛选；普通用户自动只看自己
Views.submissionList = async () => {
  const app = document.getElementById("app");
  app.innerHTML = `<div class="card"><h2>评测记录</h2><p class="muted">加载中…</p></div>`;

  const params = hashParams();
  const query = {};
  if (params.get("problem_id")) query.problem_id = params.get("problem_id");
  if (params.get("status")) query.status = params.get("status");
  if (currentUser && !isAdmin()) query.user_id = currentUser.user_id;
  const qs = new URLSearchParams(query).toString();

  let data;
  try {
    data = await api.get(`/api/submissions/${qs ? "?" + qs : ""}`);
  } catch (err) {
    app.innerHTML = err.code === 401
      ? unauthHtml()
      : `<div class="card">加载失败：${escapeHtml(err.message)}</div>`;
    return;
  }

  // 筛选链接：保留当前 problem_id 筛选条件
  const filterHref = (status) => {
    const qs = new URLSearchParams();
    if (params.get("problem_id")) qs.set("problem_id", params.get("problem_id"));
    if (status) qs.set("status", status);
    const s = qs.toString();
    return `#/submissions${s ? "?" + s : ""}`;
  };

  // 分段筛选（LeetCode 风格 tab）：active 状态由 hash 中的 status 参数决定
  const seg = (status, text) =>
    `<a href="${filterHref(status)}" class="${(params.get("status") || "") === status ? "active" : ""}">${text}</a>`;

  // 列数：管理员多一列「用户」，空状态 colspan 按实际列数计算
  const cols = isAdmin() ? 7 : 6;

  const rows = data.submissions
    .map((s) => {
      // pending/error 记录只返回 id 和 status（api.md），其余列用占位
      const rest = s.language
        ? `<td class="pid"><a href="#/problems/${encodeURIComponent(s.problem_id)}">${escapeHtml(s.problem_id)}</a></td>
           ${isAdmin() ? `<td class="num">${s.user_id}</td>` : ""}
           <td>${escapeHtml(s.language)}</td>
           <td>${statusBadge(s.status)}</td>
           <td class="num">${s.score ?? "—"}</td>
           <td>${escapeHtml(s.submit_time || "—")}</td>`
        : `<td>—</td>${isAdmin() ? "<td>—</td>" : ""}<td>—</td><td>${statusBadge(s.status)}</td><td>—</td><td>—</td>`;
      return `<tr><td class="pid"><a href="#/submissions/${s.submission_id}">#${s.submission_id}</a></td>${rest}</tr>`;
    })
    .join("");

  app.innerHTML = `
    <div class="page-head">
      <div>
        <h1>评测记录</h1>
        <p class="sub">共 ${data.total} 条提交${params.get("problem_id") ? ` · 题目 ${escapeHtml(params.get("problem_id"))}` : ""}</p>
      </div>
    </div>
    <div class="card">
      <div class="toolbar">
        <div class="segmented">
          ${seg("", "全部")}
          ${seg("pending", "等待中")}
          ${seg("success", "通过")}
          ${seg("error", "未通过")}
        </div>
      </div>
      <table class="table">
        <thead><tr>
          <th>ID</th><th>题目</th>${isAdmin() ? "<th>用户</th>" : ""}<th>语言</th><th>状态</th><th>分数</th><th>提交时间</th>
        </tr></thead>
        <tbody>${rows || `<tr><td colspan="${cols}"><div class="empty"><div class="icon">📋</div><p>暂无提交记录</p></div></td></tr>`}</tbody>
      </table>
    </div>`;
};

// 详情：状态/分数/代码/编译与错误信息/测试点明细（可见时）；pending 自动轮询
async function loadSubmissionDetail(id) {
  const app = document.getElementById("app");

  let s;
  try {
    s = await api.get(`/api/submissions/${encodeURIComponent(id)}`);
  } catch (err) {
    app.innerHTML = err.code === 401
      ? unauthHtml()
      : `<div class="card">加载失败：${escapeHtml(err.message)}</div>`;
    return;
  }

  let logHtml = "";
  try {
    const log = await api.get(`/api/submissions/${encodeURIComponent(id)}/log`);
    if (log.details) {
      // 每个测试点一个 chip（LeetCode 风格），悬停提示时间/内存
      const chips = log.details
        .map((d) => {
          const result = String(d.result || "");
          return `<span class="verdict-case ${verdictClass(result)}" title="时间 ${d.time}s · 内存 ${d.memory}MB"><b>#${escapeHtml(d.id)}</b>${VERDICT_TEXT[result.toUpperCase()] || escapeHtml(result)}</span>`;
        })
        .join("");
      logHtml = `
        <h3>测试点明细</h3>
        <div class="verdict-strip">${chips || '<span class="muted">无</span>'}</div>`;
    } else {
      logHtml = `<h3>测试点明细</h3><p class="muted">该题测试点未公开，暂无明细。</p>`;
    }
  } catch (err) {
    logHtml = `<h3>测试点明细</h3><p class="muted">不可见：${escapeHtml(err.message)}</p>`;
  }

  // 得分醒目展示（score 非空时）：统计卡片
  const counts = s.counts || null;
  const totalCases = counts ? Object.values(counts).reduce((a, b) => a + b, 0) : null;
  const scoreHtml = s.score != null
    ? `<div class="stat-grid">
         <div class="stat-card"><div class="num">${s.score}</div><div class="lbl">得分</div></div>
         ${totalCases != null ? `<div class="stat-card"><div class="num">${totalCases}</div><div class="lbl">测试点</div></div>` : ""}
       </div>`
    : "";

  // 统计：counts 为 {AC: n, WA: n, ...}，渲染为 verdict 彩条
  const countsHtml = counts && Object.keys(counts).length
    ? `<h3>测试点统计</h3>
       <div class="verdict-strip">${Object.entries(counts).map(([r, n]) => verdictPill(r, n)).join("")}</div>`
    : "";

  const infoSection = (title, content) =>
    content ? `<h3>${title}</h3><pre class="code-dark">${escapeHtml(content)}</pre>` : "";

  app.innerHTML = `
    <div class="card">
      <div class="page-head">
        <div>
          <h1>提交 #${escapeHtml(s.submission_id)}</h1>
          <p class="sub">
            <a href="#/problems/${encodeURIComponent(s.problem_id)}" class="ptitle">题目 ${escapeHtml(s.problem_id)}</a>
            · 用户 ${escapeHtml(s.user_id)} · 语言 ${escapeHtml(s.language)} · 提交于 ${escapeHtml(s.submit_time)}
          </p>
        </div>
        <div class="actions">${statusBadge(s.status)}</div>
      </div>
      ${scoreHtml}
      ${countsHtml}
      <h3>代码</h3><pre class="code-dark">${escapeHtml(s.code)}</pre>
      ${infoSection("编译信息", s.compile_info)}
      ${infoSection("运行信息", s.run_info)}
      ${infoSection("错误信息", s.error_info)}
      ${s.status === "error" || s.status === "success" ? logHtml : ""}
      ${isAdmin() ? `<div class="actions"><button id="rejudge-btn" class="btn gray">重新评测</button></div>` : ""}
    </div>`;

  if (isAdmin()) {
    document.getElementById("rejudge-btn").onclick = async () => {
      try {
        await api.put(`/api/submissions/${encodeURIComponent(id)}/rejudge`);
        toast("已重新评测");
        loadSubmissionDetail(id);
      } catch (err) { toast(err.message, false); }
    };
  }

  // pending 状态自动轮询（异步评测，前端等结果）
  if (s.status === "pending") {
    setTimeout(() => {
      if ((location.hash || "").split("?")[0] === `#/submissions/${id}`) {
        loadSubmissionDetail(id);
      }
    }, 1000);
  }
}

Views.submissionDetail = loadSubmissionDetail;
