// AI 智能命题视图（Advance：R1 交互界面 + R2 可配置模型 + R3 实时进度/中断 + R4 用量计费）
const AI_STATUS_TEXT = { pending: "等待中", running: "执行中", done: "完成", cancelled: "已取消", failed: "失败" };

function aiStatusBadge(status) {
  const cls = { pending: "pending", running: "pending", done: "ok", cancelled: "fail", failed: "fail" }[status] || "pending";
  return `<span class="badge ${cls}">${AI_STATUS_TEXT[status] || status}</span>`;
}

let _aiCleanup = null;   // 详情页 SSE/轮询的清理函数（切走页面时停止）

Views.aiHome = async () => {
  const app = document.getElementById("app");
  if (!currentUser) { location.hash = "#/login"; return; }
  app.innerHTML = `<div class="card">加载中…</div>`;

  let cfg = {}, problems = [], tasks = [];
  try { cfg = await api.get("/api/ai/model-config") || {}; } catch (e) { /* 未配置也可打开页面 */ }
  try { problems = await api.get("/api/problems/"); } catch (e) { /* 忽略 */ }
  try { tasks = await api.get("/api/ai/problem-tasks/"); } catch (e) { /* 忽略 */ }

  app.innerHTML = `
    <div class="page-head">
      <div>
        <h1>AI 智能命题</h1>
        <p class="sub">配置大模型后，输入命题需求即可自动生成符合题库规范的题目，实时查看进度并可导入题库。</p>
      </div>
    </div>
    <div class="card">
      <h2>模型配置</h2>
      ${cfg.api_key_configured
        ? `<p class="muted">当前模型：${escapeHtml(cfg.model)}（${escapeHtml(cfg.provider_url)}）· 密钥已配置（出于安全不回显）</p>`
        : '<p class="muted">尚未配置模型。</p>'}
      <form id="ai-config-form">
        <label>提供商 URL（OpenAI 兼容 chat/completions 接口）<input name="provider_url" required placeholder="https://api.example.com/v1/chat/completions" value="${escapeHtml(cfg.provider_url || "")}"></label>
        <label>模型名称<input name="model" required placeholder="gpt-4o-mini" value="${escapeHtml(cfg.model || "")}"></label>
        <label>模型密钥<input name="api_key" type="password" required placeholder="${cfg.api_key_configured ? "密钥不回显，保存时请重新填写" : "sk-..."}"></label>
        <p class="muted">⚠ 提示：不同模型、不同时段的计费价格可能不同（部分厂商设有错峰优惠时段），请按实际调用时段的官方价格填写。</p>
        <div class="form-grid">
          <label>输入价格（元/计价单位，可选）<input name="input_price" type="number" step="any" min="0" value="${cfg.input_price ?? ""}" placeholder="留空则无法自动计算费用"></label>
          <label>输出价格（元/计价单位，可选）<input name="output_price" type="number" step="any" min="0" value="${cfg.output_price ?? ""}" placeholder="留空则无法自动计算费用"></label>
        </div>
        <label>计价单位（Token 数）<input name="price_unit" type="number" min="1" value="${cfg.price_unit ?? 1000000}"></label>
        <button type="submit" class="btn">保存配置</button>
      </form>
    </div>
    <div class="card">
      <h2>新建命题任务</h2>
      <form id="ai-task-form">
        <label>命题需求<textarea name="requirement" class="code" required placeholder="例如：出一道考查二分查找的题目，难度中等，n ≤ 10^6，包含边界测试点"></textarea></label>
        <label>参考/改编题目（可选）
          <select name="problem_id">
            <option value="">— 新题目 —</option>
            ${problems.map((p) => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.id)} · ${escapeHtml(p.title)}</option>`).join("")}
          </select>
        </label>
        <button type="submit" class="btn">创建任务</button>
      </form>
    </div>
    <div class="card">
      <h2>任务记录</h2>
      ${tasks.length ? `
        <table class="table">
          <thead><tr><th>ID</th><th>状态</th><th>进度</th><th>模型</th><th>创建时间</th></tr></thead>
          <tbody>${tasks.map((t) => {
            const pct = Math.round((t.progress || 0) * 100);
            return `
              <tr>
                <td class="pid"><a href="#/ai/tasks/${t.task_id}">#${t.task_id}</a></td>
                <td>${aiStatusBadge(t.status)}</td>
                <td>${t.status === "running"
                  ? `<div class="progress-bar progress-inline"><div class="progress-fill" style="width:${Math.max(pct, 2)}%"></div></div> <span class="muted">${pct}%</span>`
                  : "—"}</td>
                <td>${escapeHtml(t.model || "—")}</td>
                <td>${escapeHtml(t.created_at || "—")}</td>
              </tr>`;
          }).join("")}</tbody>
        </table>` : `<div class="empty"><div class="icon">✨</div><p>暂无任务，创建第一个命题任务吧</p></div>`}
    </div>`;

  document.getElementById("ai-config-form").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target;
    try {
      await api.put("/api/ai/model-config", {
        provider_url: f.provider_url.value.trim(),
        model: f.model.value.trim(),
        api_key: f.api_key.value,
        // 留空 → null：费用优先按提供方返回的费用计算，否则无法计算
        input_price: f.input_price.value === "" ? null : (parseFloat(f.input_price.value) || 0),
        output_price: f.output_price.value === "" ? null : (parseFloat(f.output_price.value) || 0),
        price_unit: parseInt(f.price_unit.value) || 1000000,
      });
      toast("模型配置已保存");
      Views.aiHome();
    } catch (err) { toast(err.message, false); }
  };

  document.getElementById("ai-task-form").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target;
    try {
      const resp = await api.post("/api/ai/problem-tasks/", {
        requirement: f.requirement.value,
        problem_id: f.problem_id.value || undefined,
      });
      toast("任务已创建");
      location.hash = `#/ai/tasks/${resp.task_id}`;
    } catch (err) { toast(err.message, false); }
  };
};

// 生成的题目预览 + 用量费用面板（R1/R4）
function renderResult(result, usage, problemId) {
  const u = usage || {};
  // 计价依据说明（advance.md 要求透明）
  const priceNote = {
    provider: "费用由模型接口直接返回。",
    config: `费用 = 输入Token/${u.price_unit} × ${u.input_price} + 输出Token/${u.price_unit} × ${u.output_price}（${u.currency}，手动配置价格）`,
    unknown: "未填写输入/输出价格，无法自动计算费用；可在模型配置中填写价格（注意不同时段价格可能不同）。",
  }[u.price_source] || "";
  return `
    <div class="card problem-desc">
      <h2>生成的题目：${escapeHtml(result.title)} <span class="muted">(${escapeHtml(result.id)})</span></h2>
      <p class="muted">
        难度 ${escapeHtml(result.difficulty || "—")} · 时间限制 ${result.time_limit}s · 内存限制 ${result.memory_limit}MB
        ${result.tags && result.tags.length ? " · " + result.tags.map((t) => `<span class="chip">${escapeHtml(t)}</span>`).join("") : ""}
      </p>
      <h3>题目描述</h3>${renderMarkdown(result.description)}
      <h3>输入格式</h3>${renderMarkdown(result.input_description)}
      <h3>输出格式</h3>${renderMarkdown(result.output_description)}
      ${(result.samples || []).length ? `
        <h3>样例</h3>
        <div class="sample-grid">
          ${result.samples.map((s, i) => `
            <div class="sample-box">
              <div class="sample-head"><span>样例 ${i + 1} · 输入</span><button type="button" class="copy-btn" data-copy="${escapeHtml(s.input)}">复制</button></div>
              <pre>${escapeHtml(s.input)}</pre>
            </div>
            <div class="sample-box">
              <div class="sample-head"><span>样例 ${i + 1} · 输出</span><button type="button" class="copy-btn" data-copy="${escapeHtml(s.output)}">复制</button></div>
              <pre>${escapeHtml(s.output)}</pre>
            </div>`).join("")}
        </div>` : ""}
      <h3>数据范围</h3>${renderMarkdown(result.constraints)}
      <h3>测试点（${result.testcases.length} 个）</h3>
      <div class="verdict-strip">${result.testcases.map((t) => `<span class="verdict-case unk"><b>#${escapeHtml(t.id || "?")}</b>测试点</span>`).join("")}</div>
      ${result.hint ? `<h3>提示</h3>${renderMarkdown(result.hint)}` : ""}
      <div class="actions">
        <button id="ai-import-btn" class="btn">${problemId ? `保存修改到 ${escapeHtml(problemId)}` : "保存为新题目"}</button>
      </div>
    </div>
    <div class="card">
      <h2>Token 用量与费用</h2>
      <table class="table">
        <tr><td>输入 Token</td><td class="num">${u.input_tokens ?? "—"}</td></tr>
        <tr><td>输出 Token</td><td class="num">${u.output_tokens ?? "—"}</td></tr>
        <tr><td>总 Token</td><td class="num">${u.total_tokens ?? "—"}</td></tr>
        <tr><td>费用</td><td class="num">${u.cost == null ? "—" : `${u.cost} ${escapeHtml(u.currency || "")}`}</td></tr>
      </table>
      <p class="muted">计价依据：${escapeHtml(priceNote || "未配置价格")}${u.estimated ? "；模型接口未返回 Token 用量，按字符数/4 估算。" : ""}</p>
    </div>`;
}

// 导入题库：走已有题目接口（不直接写 ProblemStore，与基础功能解耦）
function bindImport(result, problemId) {
  const btn = document.getElementById("ai-import-btn");
  if (!btn) return;
  btn.onclick = async () => {
    try {
      if (problemId) {
        await api.put(`/api/problems/${encodeURIComponent(problemId)}`, result);
        toast("已保存修改");
        location.hash = `#/problems/${encodeURIComponent(problemId)}`;
      } else {
        const resp = await api.post("/api/problems/", result);
        toast("已保存为新题目");
        location.hash = `#/problems/${encodeURIComponent(resp.id)}`;
      }
    } catch (err) { toast(err.message, false); }
  };
}

Views.aiTaskDetail = async (id) => {
  const app = document.getElementById("app");
  if (!currentUser) { location.hash = "#/login"; return; }
  if (_aiCleanup) { _aiCleanup(); _aiCleanup = null; }

  const onRoute = () => (location.hash || "").split("?")[0].startsWith(`#/ai/tasks/${id}`);

  let s;
  try {
    s = await api.get(`/api/ai/problem-tasks/${id}`);
  } catch (err) {
    app.innerHTML = err.code === 401
      ? unauthHtml()
      : `<div class="card">加载失败：${escapeHtml(err.message)}</div>`;
    return;
  }

  const render = (data) => {
    if (!onRoute()) return;
    const pct = Math.round((data.progress || 0) * 100);
    const active = data.status === "pending" || data.status === "running";
    app.innerHTML = `
      <div class="card">
        <div class="page-head">
          <div>
            <h1>AI 命题任务 #${data.task_id}</h1>
            <p class="sub">${escapeHtml(data.requirement)}${data.problem_id ? `（改编自 ${escapeHtml(data.problem_id)}）` : ""}</p>
            <p class="sub">模型 ${escapeHtml(data.model || "—")} · 创建于 ${escapeHtml(data.created_at || "—")}</p>
          </div>
          <div class="actions">${aiStatusBadge(data.status)}</div>
        </div>
        <div class="progress-bar">
          <div class="progress-fill ${data.status === "done" ? "done" : (data.status === "failed" || data.status === "cancelled") ? "fail" : ""}" style="width:${active ? Math.max(pct, 2) : (data.status === "done" ? 100 : pct)}%"></div>
        </div>
        <p class="muted" id="ai-msg">${escapeHtml(data.message || AI_STATUS_TEXT[data.status] || "")}</p>
        ${active ? '<p><button id="ai-cancel-btn" class="btn danger">中断任务</button></p>' : ""}
      </div>
      <div id="ai-extra"></div>`;
    const extra = document.getElementById("ai-extra");
    if (data.status === "done" && data.result) {
      extra.innerHTML = renderResult(data.result, data.usage, data.problem_id);
      bindImport(data.result, data.problem_id);
      bindCopyButtons(extra);   // 样例复制按钮（renderResult 生成的 copy-btn）
    } else if (data.status === "failed") {
      extra.innerHTML = `<div class="card"><h2>任务失败</h2><pre class="code-dark">${escapeHtml((data.result && data.result.error) || "未知错误")}</pre></div>`;
    } else if (data.status === "cancelled") {
      extra.innerHTML = `<div class="card empty"><div class="icon">🛑</div><p>任务已中断，后台执行已终止，可返回 AI 命题页重新创建任务。</p></div>`;
    }
    const cancelBtn = document.getElementById("ai-cancel-btn");
    if (cancelBtn) cancelBtn.onclick = async () => {
      try {
        await api.put(`/api/ai/problem-tasks/${id}/cancel`);
        toast("已中断任务");
        render(await api.get(`/api/ai/problem-tasks/${id}`));
      } catch (err) { toast(err.message, false); }
    };
  };

  render(s);
  if (s.status !== "pending" && s.status !== "running") return;

  // 实时进度：优先 SSE，连接失败时退回轮询（api.md 允许任一方案）
  const finalize = (data) => {
    if (_aiCleanup) { _aiCleanup(); _aiCleanup = null; }
    render(data);
  };
  const es = new EventSource(`/api/ai/problem-tasks/${id}/events`);
  es.addEventListener("state", (e) => render({ ...s, ...JSON.parse(e.data) }));
  es.addEventListener("progress", (e) => render({ ...s, ...JSON.parse(e.data) }));
  es.addEventListener("final", (e) => finalize({ ...s, ...JSON.parse(e.data) }));
  es.onerror = () => {
    es.close();
    if (_aiCleanup) return;   // final 已触发过，避免重复回退
    const timer = setInterval(async () => {
      if (!onRoute()) { clearInterval(timer); _aiCleanup = null; return; }
      try {
        const d = await api.get(`/api/ai/problem-tasks/${id}`);
        render(d);
        if (d.status !== "pending" && d.status !== "running") {
          clearInterval(timer); _aiCleanup = null;
        }
      } catch (err) {
        clearInterval(timer); _aiCleanup = null;
        app.innerHTML = `<div class="card">加载失败：${escapeHtml(err.message)}</div>`;
      }
    }, 1500);
    _aiCleanup = () => clearInterval(timer);
  };
  _aiCleanup = () => es.close();
};
