// 题目视图：列表 / 详情 / 新建与编辑（Step 1 + Step 6 前端，洛谷 / LeetCode 风格版式）
Views.problemList = async () => {
  const app = document.getElementById("app");
  // 加载骨架：页面头 + 加载提示卡片
  app.innerHTML = `
    <div class="page-head">
      <div>
        <h1>题目列表</h1>
        <div class="sub">加载中…</div>
      </div>
      ${currentUser ? `<div class="actions"><a href="#/problems/new" class="btn">新建题目</a></div>` : ""}
    </div>
    <div class="card"><p class="muted">加载中…</p></div>`;

  let problems;
  try {
    problems = await api.get("/api/problems/");
  } catch (err) {
    if (err.code === 401) {
      app.innerHTML = unauthHtml("请先登录后查看题目");
      return;
    }
    app.innerHTML = `<div class="card">加载失败：${escapeHtml(err.message)}</div>`;
    return;
  }

  // 表格行渲染（初次渲染与搜索过滤共用；所有用户可控字段均转义）
  const rows = (list) => list
    .map((p) => {
      const href = `#/problems/${encodeURIComponent(p.id)}`;
      const tags = (p.tags || []).map((t) => `<span class="chip">${escapeHtml(t)}</span>`).join("");
      return `<tr>
        <td class="pid">${escapeHtml(p.id)}</td>
        <td>
          <a class="ptitle" href="${href}">${escapeHtml(p.title)}</a>
          ${tags ? `<div>${tags}</div>` : ""}
        </td>
        <td>${diffChip(p.difficulty)}</td>
        <td class="muted">${escapeHtml(p.source || "—")} · ${escapeHtml(p.author || "—")}</td>
      </tr>`;
    })
    .join("");

  const emptyRow = (msg) => `<tr><td colspan="4"><div class="empty">${msg}</div></td></tr>`;

  app.innerHTML = `
    <div class="page-head">
      <div>
        <h1>题目列表</h1>
        <div class="sub">共 ${problems.length} 题</div>
      </div>
      ${currentUser ? `<div class="actions"><a href="#/problems/new" class="btn">新建题目</a></div>` : ""}
    </div>
    <div class="toolbar">
      <input id="search-input" class="search" placeholder="搜索题目 ID / 标题 / 标签">
    </div>
    <div class="card">
      <table class="table">
        <thead><tr><th>ID</th><th>标题</th><th>难度</th><th>来源 / 作者</th></tr></thead>
        <tbody id="problem-rows">${problems.length ? rows(problems) : emptyRow("暂无题目")}</tbody>
      </table>
    </div>`;

  // 客户端实时搜索：按 ID / 标题 / 标签 / 难度 / 作者过滤已加载的题目列表
  const tbody = document.getElementById("problem-rows");
  document.getElementById("search-input").oninput = (e) => {
    const q = e.target.value.trim().toLowerCase();
    const list = problems.filter((p) =>
      [p.id, p.title, (p.tags || []).join(" "), p.difficulty, p.author]
        .some((v) => String(v || "").toLowerCase().includes(q)));
    tbody.innerHTML = list.length ? rows(list) : emptyRow("未找到匹配的题目");
  };
};

Views.problemDetail = async (id) => {
  const app = document.getElementById("app");
  app.innerHTML = `<div class="card">加载中…</div>`;

  let p;
  try {
    p = await api.get(`/api/problems/${encodeURIComponent(id)}`);
  } catch (err) {
    app.innerHTML = err.code === 401
      ? unauthHtml("请先登录后查看题目")
      : `<div class="card">加载失败：${escapeHtml(err.message)}</div>`;
    return;
  }

  const tags = (p.tags || []).map((t) => `<span class="chip">${escapeHtml(t)}</span>`).join("");
  // 样例双栏（洛谷风格）：每个样例渲染输入 / 输出两个对照框，右上角复制按钮
  const samples = (p.samples || [])
    .map((s, i) => `
      <div class="sample-box">
        <div class="sample-head">样例 ${i + 1} · 输入
          <button class="copy-btn" data-copy="${escapeHtml(s.input)}">复制</button>
        </div>
        <pre>${escapeHtml(s.input)}</pre>
      </div>
      <div class="sample-box">
        <div class="sample-head">样例 ${i + 1} · 输出
          <button class="copy-btn" data-copy="${escapeHtml(s.output)}">复制</button>
        </div>
        <pre>${escapeHtml(s.output)}</pre>
      </div>`)
    .join("");

  app.innerHTML = `
    <div class="card problem-desc">
        <h1>${escapeHtml(p.title)} ${diffChip(p.difficulty)}</h1>
        ${tags ? `<p class="tags-row">${tags}</p>` : ""}
        <p class="muted meta-line">
          题目 ID ${escapeHtml(p.id)} · 时间限制 ${p.time_limit}s · 内存限制 ${p.memory_limit}MB${p.author ? ` · 作者 ${escapeHtml(p.author)}` : ""}
        </p>
        <h3>题目描述</h3>${renderMarkdown(p.description)}
        <h3>输入格式</h3>${renderMarkdown(p.input_description)}
        <h3>输出格式</h3>${renderMarkdown(p.output_description)}
        ${samples ? `<h3>样例</h3><div class="sample-grid">${samples}</div>` : ""}
        <h3>数据范围</h3>${renderMarkdown(p.constraints)}
        ${p.hint ? `<h3>提示</h3>${renderMarkdown(p.hint)}` : ""}
        <div class="actions">
          ${currentUser ? `<a href="#/problems/${encodeURIComponent(p.id)}/edit" class="btn gray">编辑</a>` : ""}
          ${isAdmin() ? `<button id="del-btn" class="btn danger">删除</button>` : ""}
          <a href="#/submissions?problem_id=${encodeURIComponent(p.id)}" class="btn gray">提交记录</a>
        </div>
    </div>
    <div class="card" id="submit-panel"></div>`;

  // 样例复制按钮（data-copy 已随 escapeHtml 转义引号）
  bindCopyButtons();

  // 提交面板（登录后可提交，Step 6）
  const panel = document.getElementById("submit-panel");
  if (!currentUser) {
    panel.innerHTML = `<h2>提交代码</h2><p class="muted">请先<a href="#/login">登录</a>后提交。</p>`;
  } else {
    let languages = [];
    try {
      const resp = await api.get("/api/languages/");
      languages = resp.name || [];
    } catch (err) { /* 忽略，下方提示 */ }
    panel.innerHTML = `
      <h2>提交代码</h2>
      <form id="submit-form">
        <label>语言
          <select name="language">${languages.map((l) => `<option value="${escapeHtml(l)}">${escapeHtml(l)}</option>`).join("")}</select>
        </label>
        <label>代码<textarea name="code" class="code" required placeholder="在此粘贴你的代码"></textarea></label>
        <button type="submit" class="btn">提交评测</button>
      </form>
      ${languages.length === 0 ? '<p class="muted">暂无可用语言，请联系管理员注册。</p>' : ""}`;
    document.getElementById("submit-form").onsubmit = async (e) => {
      e.preventDefault();
      const f = e.target;
      try {
        const resp = await api.post("/api/submissions/", {
          problem_id: p.id, language: f.language.value, code: f.code.value,
        });
        toast("提交成功，等待评测…");
        location.hash = `#/submissions/${resp.submission_id}`;
      } catch (err) { toast(err.message, false); }
    };
  }

  if (isAdmin()) {
    document.getElementById("del-btn").onclick = async () => {
      if (!confirm(`确认删除题目 ${p.id}？`)) return;
      try {
        await api.del(`/api/problems/${encodeURIComponent(p.id)}`);
        toast("已删除");
        location.hash = "#/problems";
      } catch (err) { toast(err.message, false); }
    };
  }
};

// 新建（无 id）或编辑（有 id）。samples / testcases 暂用 JSON 文本编辑，
// 后续可改为可视化编辑器。
Views.problemEdit = async (id) => {
  const isEdit = !!id;
  let p = {
    id: "", title: "", description: "", input_description: "", output_description: "",
    samples: [], constraints: "", testcases: [],
    hint: "", source: "", tags: [], time_limit: 3, memory_limit: 128, author: "", difficulty: "",
  };

  if (isEdit) {
    try {
      p = await api.get(`/api/problems/${encodeURIComponent(id)}`);
    } catch (err) {
      toast(err.message, false);
      location.hash = "#/problems";
      return;
    }
  }

  const app = document.getElementById("app");
  app.innerHTML = `
    <div class="card">
      <h2>${isEdit ? "编辑题目" : "新建题目"}</h2>
      <form id="problem-form">
        <label>ID（唯一标识）<input name="id" required ${isEdit ? "disabled" : ""} value="${escapeHtml(p.id)}"></label>
        <label>标题<input name="title" required value="${escapeHtml(p.title)}"></label>
        <label>题目描述（支持 Markdown 语法）<textarea name="description" required>${escapeHtml(p.description)}</textarea></label>
        <label>输入格式（支持 Markdown 语法）<textarea name="input_description" required>${escapeHtml(p.input_description)}</textarea></label>
        <label>输出格式（支持 Markdown 语法）<textarea name="output_description" required>${escapeHtml(p.output_description)}</textarea></label>
        <label>样例（JSON 数组：[{"input": "...", "output": "..."}]）<textarea name="samples" class="code" required>${escapeHtml(JSON.stringify(p.samples, null, 2))}</textarea></label>
        <label>数据范围（支持 Markdown 语法）<textarea name="constraints" required>${escapeHtml(p.constraints)}</textarea></label>
        <label>测试点（JSON 数组）<textarea name="testcases" class="code" required>${escapeHtml(JSON.stringify(p.testcases, null, 2))}</textarea></label>
        <label>提示（支持 Markdown 语法）<input name="hint" value="${escapeHtml(p.hint)}"></label>
        <label>来源<input name="source" value="${escapeHtml(p.source)}"></label>
        <label>标签（逗号分隔）<input name="tags" value="${escapeHtml(p.tags.join(", "))}"></label>
        <div class="form-grid">
          <label>时间限制（秒）<input name="time_limit" type="number" step="0.1" value="${p.time_limit}"></label>
          <label>内存限制（MB）<input name="memory_limit" type="number" value="${p.memory_limit}"></label>
          <label>作者<input name="author" value="${escapeHtml(p.author)}"></label>
          <label>难度<input name="difficulty" value="${escapeHtml(p.difficulty)}"></label>
        </div>
        <div class="actions">
          <button type="submit" class="btn">保存</button>
          <a href="${isEdit ? `#/problems/${encodeURIComponent(id)}` : "#/problems"}" class="btn gray">取消</a>
        </div>
      </form>
    </div>`;

  document.getElementById("problem-form").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target;
    const body = {
      id: isEdit ? id : f.id.value.trim(),
      title: f.title.value,
      description: f.description.value,
      input_description: f.input_description.value,
      output_description: f.output_description.value,
      constraints: f.constraints.value,
      hint: f.hint.value,
      source: f.source.value,
      tags: f.tags.value.split(",").map((s) => s.trim()).filter(Boolean),
      time_limit: parseFloat(f.time_limit.value),
      memory_limit: parseInt(f.memory_limit.value),
      author: f.author.value,
      difficulty: f.difficulty.value,
    };
    try {
      body.samples = JSON.parse(f.samples.value);
      body.testcases = JSON.parse(f.testcases.value);
    } catch {
      toast("samples / testcases 必须是合法 JSON 数组", false);
      return;
    }
    try {
      const resp = isEdit
        ? await api.put(`/api/problems/${encodeURIComponent(id)}`, body)
        : await api.post("/api/problems/", body);
      toast("保存成功");
      location.hash = `#/problems/${encodeURIComponent(resp.id)}`;
    } catch (err) { toast(err.message, false); }
  };
};
