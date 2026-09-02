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

  const rows = data.submissions
    .map((s) => {
      // pending/error 记录只返回 id 和 status（api.md）
      const rest = s.language
        ? `<td>${escapeHtml(s.problem_id)}</td>
           ${isAdmin() ? `<td>${s.user_id}</td>` : ""}
           <td>${escapeHtml(s.language)}</td>
           <td>${statusBadge(s.status)}</td>
           <td>${s.score ?? "—"}</td>
           <td>${escapeHtml(s.submit_time || "—")}</td>`
        : `<td>—</td>${isAdmin() ? "<td>—</td>" : ""}<td>—</td><td>${statusBadge(s.status)}</td><td>—</td><td>—</td>`;
      return `<tr><td><a href="#/submissions/${s.submission_id}">#${s.submission_id}</a></td>${rest}</tr>`;
    })
    .join("");

  app.innerHTML = `
    <div class="card">
      <h2>评测记录 <span class="muted">共 ${data.total} 条</span></h2>
      <p>
        <a href="${filterHref()}" class="btn gray ${!params.get("status") ? "active" : ""}">全部</a>
        <a href="${filterHref("pending")}" class="btn gray ${params.get("status") === "pending" ? "active" : ""}">等待中</a>
        <a href="${filterHref("success")}" class="btn gray ${params.get("status") === "success" ? "active" : ""}">通过</a>
        <a href="${filterHref("error")}" class="btn gray ${params.get("status") === "error" ? "active" : ""}">未通过</a>
      </p>
      <table class="table">
        <thead><tr>
          <th>ID</th><th>题目</th>${isAdmin() ? "<th>用户</th>" : ""}<th>语言</th><th>状态</th><th>分数</th><th>提交时间</th>
        </tr></thead>
        <tbody>${rows || '<tr><td colspan="7">暂无记录</td></tr>'}</tbody>
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
      const rows = log.details
        .map((d) => `<tr><td>${escapeHtml(d.id)}</td><td>${escapeHtml(d.result)}</td><td>${d.time}s</td><td>${d.memory}MB</td></tr>`)
        .join("");
      logHtml = `
        <h3>测试点明细</h3>
        <table class="table">
          <thead><tr><th>测试点</th><th>结果</th><th>时间</th><th>内存</th></tr></thead>
          <tbody>${rows || "<tr><td colspan='4'>无</td></tr>"}</tbody>
        </table>`;
    } else {
      logHtml = `<h3>测试点明细</h3><p class="muted">该题测试点未公开，暂无明细。</p>`;
    }
  } catch (err) {
    logHtml = `<h3>测试点明细</h3><p class="muted">不可见：${escapeHtml(err.message)}</p>`;
  }

  const infoSection = (title, content) =>
    content ? `<h3>${title}</h3><pre class="wrap">${escapeHtml(content)}</pre>` : "";

  app.innerHTML = `
    <div class="card">
      <h2>提交 #${escapeHtml(s.submission_id)} ${statusBadge(s.status)}</h2>
      <p class="muted">
        <a href="#/problems/${encodeURIComponent(s.problem_id)}">题目 ${escapeHtml(s.problem_id)}</a>
        · 用户 ${s.user_id} · 语言 ${escapeHtml(s.language)} · 提交于 ${escapeHtml(s.submit_time)}
      </p>
      <h3>得分</h3><p>${s.score ?? "—"}</p>
      <h3>统计</h3><pre>${escapeHtml(JSON.stringify(s.counts ?? null))}</pre>
      <h3>代码</h3><pre class="wrap code-block">${escapeHtml(s.code)}</pre>
      ${infoSection("编译信息", s.compile_info)}
      ${infoSection("运行信息", s.run_info)}
      ${infoSection("错误信息", s.error_info)}
      ${s.status === "error" || s.status === "success" ? logHtml : ""}
      <div class="actions">
        ${isAdmin() ? `<button id="rejudge-btn" class="btn gray">重新评测</button>` : ""}
        <a href="#/submissions" class="btn gray">返回列表</a>
      </div>
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
