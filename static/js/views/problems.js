// 题目视图：列表 / 详情 / 新建与编辑（Step 1 + Step 6 前端）
Views.problemList = async () => {
  document.getElementById("app").innerHTML =
    `<div class="card"><h2>题目列表</h2><p id="plist" class="muted">加载中…</p></div>`;

  let problems;
  try {
    problems = await api.get("/api/problems/");
  } catch (err) {
    document.getElementById("plist").textContent = `加载失败：${err.message}`;
    return;
  }

  const rows = problems
    .map((p) => `<tr><td><a href="#/problems/${encodeURIComponent(p.id)}">${escapeHtml(p.id)}</a></td><td>${escapeHtml(p.title)}</td></tr>`)
    .join("");
  document.getElementById("app").innerHTML = `
    <div class="card">
      <h2>题目列表</h2>
      ${currentUser ? `<p><a href="#/problems/new" class="btn">新建题目</a></p>` : ""}
      <table class="table">
        <thead><tr><th>ID</th><th>标题</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="2">暂无题目</td></tr>'}</tbody>
      </table>
    </div>`;
};

Views.problemDetail = async (id) => {
  const app = document.getElementById("app");
  app.innerHTML = `<div class="card">加载中…</div>`;

  let p;
  try {
    p = await api.get(`/api/problems/${encodeURIComponent(id)}`);
  } catch (err) {
    app.innerHTML = `<div class="card">加载失败：${escapeHtml(err.message)}</div>`;
    return;
  }

  const samples = p.samples
    .map((s, i) => `<div class="sample"><h3>样例 ${i + 1}</h3><pre>输入\n${escapeHtml(s.input)}\n\n输出\n${escapeHtml(s.output)}</pre></div>`)
    .join("");
  const tags = p.tags.map((t) => `<span class="badge">${escapeHtml(t)}</span>`).join(" ");

  app.innerHTML = `
    <div class="card">
      <h2>${escapeHtml(p.title)} <span class="muted">(${escapeHtml(p.id)})</span></h2>
      <p class="muted">
        时间限制 ${p.time_limit}s · 内存限制 ${p.memory_limit}MB
        ${p.author ? ` · 作者 ${escapeHtml(p.author)}` : ""}
        ${p.difficulty ? ` · 难度 ${escapeHtml(p.difficulty)}` : ""}
      </p>
      ${tags ? `<p>${tags}</p>` : ""}
      <h3>题目描述</h3><pre class="wrap">${escapeHtml(p.description)}</pre>
      <h3>输入格式</h3><pre class="wrap">${escapeHtml(p.input_description)}</pre>
      <h3>输出格式</h3><pre class="wrap">${escapeHtml(p.output_description)}</pre>
      ${samples}
      <h3>数据范围</h3><pre class="wrap">${escapeHtml(p.constraints)}</pre>
      ${p.hint ? `<h3>提示</h3><pre class="wrap">${escapeHtml(p.hint)}</pre>` : ""}
      <div class="actions">
        ${currentUser ? `<a href="#/problems/${encodeURIComponent(p.id)}/edit" class="btn gray">编辑</a>` : ""}
        ${isAdmin() ? `<button id="del-btn" class="btn danger">删除</button>` : ""}
        <a href="#/submissions?problem_id=${encodeURIComponent(p.id)}" class="btn gray">提交记录</a>
      </div>
    </div>
    <div class="card" id="submit-panel"></div>`;

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
        <label>题目描述<textarea name="description" required>${escapeHtml(p.description)}</textarea></label>
        <label>输入格式<textarea name="input_description" required>${escapeHtml(p.input_description)}</textarea></label>
        <label>输出格式<textarea name="output_description" required>${escapeHtml(p.output_description)}</textarea></label>
        <label>样例（JSON 数组：[{"input": "...", "output": "..."}]）<textarea name="samples" class="code" required>${escapeHtml(JSON.stringify(p.samples, null, 2))}</textarea></label>
        <label>数据范围<textarea name="constraints" required>${escapeHtml(p.constraints)}</textarea></label>
        <label>测试点（JSON 数组）<textarea name="testcases" class="code" required>${escapeHtml(JSON.stringify(p.testcases, null, 2))}</textarea></label>
        <label>提示<input name="hint" value="${escapeHtml(p.hint)}"></label>
        <label>来源<input name="source" value="${escapeHtml(p.source)}"></label>
        <label>标签（逗号分隔）<input name="tags" value="${escapeHtml(p.tags.join(", "))}"></label>
        <label>时间限制（秒）<input name="time_limit" type="number" step="0.1" value="${p.time_limit}"></label>
        <label>内存限制（MB）<input name="memory_limit" type="number" value="${p.memory_limit}"></label>
        <label>作者<input name="author" value="${escapeHtml(p.author)}"></label>
        <label>难度<input name="difficulty" value="${escapeHtml(p.difficulty)}"></label>
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
