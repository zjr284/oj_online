"""Step 6 前端：Streamlit 页面，通过 REST API 对接 FastAPI 后端（api.md 接口标准）。

运行：streamlit run app.py（后端需已在 8000 端口运行，可用 OJ_API_BASE 环境变量指向其它地址）

要求（step6.md）：
- 任务 1 用户页面组：注册/登录/退出、用户信息展示、用户管理（仅管理员）
- 任务 2 题目页面组：列表/详情/新增/编辑/删除，表单提交前做格式检查
- 任务 3 评测与提交页面组：代码提交、提交记录列表/详情、轮询评测状态、明确展示 CE/RE/TLE 等
- 任务 4 接口对接：统一 API 封装、身份存 session_state（会话 Cookie 经 httpx 传递）、
  不硬编码用户身份、按响应 code/msg 展示成败、不绕过后端直接读写数据
"""

import json
import os
import time

import httpx
import pandas as pd
import streamlit as st

API_BASE = os.environ.get("OJ_API_BASE", "http://127.0.0.1:8000")

st.set_page_config(page_title="Online Judge", page_icon="⚖️", layout="wide")

STATUS_TEXT = {"pending": "⏳ 等待评测", "success": "✅ 通过", "error": "❌ 未通过"}
VERDICT_TEXT = {
    "AC": "通过", "WA": "答案错误", "TLE": "超出时间限制",
    "MLE": "超出内存限制", "RE": "运行时错误", "CE": "编译错误",
}
ROLE_TEXT = {"user": "用户", "admin": "管理员", "banned": "封禁"}


# ---------- 统一 API 封装（任务 4） ----------

class ApiError(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code = code
        self.msg = msg


def _clear_session():
    """清除本地登录态与页面状态（登出/会话过期时）。"""
    for key in ("me", "cookies", "nav", "prob_view", "prob_id",
                "sub_view", "sub_id", "confirm_delete", "submit_problem"):
        st.session_state.pop(key, None)


def api(method: str, path: str, **kwargs):
    """统一 API 调用：自动携带会话 Cookie，解析 {code, msg, data} 协议。

    - 成功返回 data；失败抛 ApiError（code/msg 与后端一致）
    - 401 视为会话失效，自动清除本地登录态
    - 后端不可达抛 ApiError(0, …)
    """
    cookies = st.session_state.get("cookies", {})
    try:
        resp = httpx.request(method, API_BASE + path, cookies=cookies, timeout=30, **kwargs)
    except httpx.HTTPError as e:
        raise ApiError(0, f"无法连接后端（{API_BASE}）：{e}") from e
    try:
        body = resp.json()
    except ValueError:
        raise ApiError(resp.status_code, f"后端返回非 JSON（HTTP {resp.status_code}）") from None
    st.session_state["cookies"] = dict(resp.cookies) or cookies   # 登录/登出会下发新 Cookie
    if body.get("code") != 200:
        if body.get("code") == 401:
            _clear_session()
        raise ApiError(body.get("code"), body.get("msg", f"HTTP {resp.status_code}"))
    return body.get("data")


def friendly_error(err: ApiError):
    """失败友好提示：401 提示登录、429 提示限流，其余展示后端 msg。"""
    if err.code == 401:
        st.warning("尚未登录或会话已过期，请先在左侧登录。")
    elif err.code == 429:
        st.error("操作过于频繁，请稍后再试。")
    else:
        st.error(f"操作失败（{err.code}）：{err.msg}")


# ---------- 侧边栏：登录态 + 导航 ----------

def render_sidebar() -> str:
    me = st.session_state.get("me")
    with st.sidebar:
        st.markdown("## ⚖️ Online Judge")
        st.caption("FastAPI 异步 OJ · Streamlit 前端")
        st.divider()
        if me:
            st.markdown(f"**{me.get('username')}**　{'🔑 管理员' if me.get('role') == 'admin' else '👤 用户'}")
            st.divider()
            pages = ["📋 题目", "🚀 提交评测", "📜 评测记录", "🙍 个人主页"]
            if me.get("role") == "admin":
                pages.append("🛠 用户管理")
            nav = st.session_state.get("nav")
            if nav not in pages:
                nav = pages[0]
            page = st.radio("导航", pages, index=pages.index(nav), label_visibility="collapsed")
            st.session_state["nav"] = page
            if st.button("退出登录", width="stretch"):
                try:
                    api("POST", "/api/auth/logout")
                except ApiError:
                    pass   # 会话已失效也无妨
                _clear_session()
                st.rerun()
            return page
        st.markdown("尚未登录，请先登录后使用。")
        return st.radio("导航", ["🔑 登录", "📝 注册"], label_visibility="collapsed")


# ---------- 任务 1：用户页面组 ----------

def page_login():
    st.title("🔑 登录")
    with st.form("login-form"):
        username = st.text_input("用户名")
        password = st.text_input("密码", type="password")
        submitted = st.form_submit_button("登录", width="stretch")
    if not submitted:
        return
    if not username.strip() or not password:
        st.error("请输入用户名和密码。")
        return
    try:
        me = api("POST", "/api/auth/login", json={"username": username.strip(), "password": password})
    except ApiError as e:
        if e.code == 401:
            st.error("用户名或密码错误，请重试。")   # 登录失败友好提示
        elif e.code == 403:
            st.error("该账号已被禁用，请联系管理员。")
        else:
            friendly_error(e)
        return
    st.session_state["me"] = me
    st.success(f"欢迎回来，{me['username']}！")
    time.sleep(0.5)
    st.rerun()


def page_register():
    st.title("📝 注册")
    with st.form("register-form"):
        username = st.text_input("用户名（3–40 字符）")
        password = st.text_input("密码（至少 6 位）", type="password")
        submitted = st.form_submit_button("注册", width="stretch")
    if not submitted:
        return
    if not (3 <= len(username.strip()) <= 40):
        st.error("用户名长度需在 3–40 字符之间。")
        return
    if len(password) < 6:
        st.error("密码至少 6 位。")
        return
    try:
        api("POST", "/api/users/", json={"username": username.strip(), "password": password})
    except ApiError as e:
        friendly_error(e)
        return
    # 注册成功后自动登录
    me = api("POST", "/api/auth/login", json={"username": username.strip(), "password": password})
    st.session_state["me"] = me
    st.success(f"注册成功，欢迎你，{me['username']}！")
    time.sleep(0.5)
    st.rerun()


def page_profile():
    me = st.session_state.get("me")
    st.title("🙍 个人主页")
    try:
        u = api("GET", f"/api/users/{me['user_id']}")
    except ApiError as e:
        friendly_error(e)
        return
    st.markdown(f"### {u['username']}　·　{ROLE_TEXT.get(u.get('role'), u.get('role'))}")
    st.caption(f"注册时间：{u.get('join_time')}")
    submit_count = int(u.get("submit_count") or 0)
    resolve_count = int(u.get("resolve_count") or 0)
    rate = f"{resolve_count / submit_count * 100:.1f}%" if submit_count else "—"
    c1, c2, c3 = st.columns(3)
    c1.metric("提交数", submit_count)
    c2.metric("通过题数", resolve_count)
    c3.metric("通过率", rate)


def page_admin_users():
    st.title("🛠 用户管理")
    try:
        data = api("GET", "/api/users/")
    except ApiError as e:
        friendly_error(e)
        return
    users = data.get("users", [])
    st.caption(f"共 {data.get('total', 0)} 人")
    if users:
        st.dataframe(pd.DataFrame([
            {"ID": u.get("user_id"), "用户名": u.get("username"),
             "角色": ROLE_TEXT.get(u.get("role"), u.get("role")),
             "注册时间": u.get("join_time"), "提交数": u.get("submit_count"),
             "通过题数": u.get("resolve_count")}
            for u in users
        ]), width="stretch", hide_index=True)

    st.divider()
    if not users:
        st.info("暂无用户。")
        return
    st.subheader("修改用户角色")
    sel = st.selectbox(
        "选择用户", users,
        format_func=lambda u: f"#{u.get('user_id')} · {u.get('username')}（{ROLE_TEXT.get(u.get('role'), u.get('role'))}）",
    )
    role = st.selectbox("新角色", ["user", "admin", "banned"], format_func=ROLE_TEXT.get)
    if st.button("修改角色"):
        if role == sel.get("role"):
            st.info("角色未发生变化。")
        else:
            try:
                api("PUT", f"/api/users/{sel['user_id']}/role", json={"role": role})
                st.success(f"已将用户 {sel['username']} 的角色改为 {ROLE_TEXT[role]}。")
                time.sleep(0.4)
                st.rerun()
            except ApiError as e:
                friendly_error(e)

    st.divider()
    st.subheader("创建管理员")
    with st.form("create-admin-form"):
        username = st.text_input("用户名（3–40 字符）")
        password = st.text_input("密码（至少 6 位）", type="password")
        if st.form_submit_button("创建管理员"):
            if not (3 <= len(username.strip()) <= 40) or len(password) < 6:
                st.error("用户名需 3–40 字符，密码至少 6 位。")
            else:
                try:
                    api("POST", "/api/users/admin", json={"username": username.strip(), "password": password})
                    st.success("已创建管理员。")
                    time.sleep(0.4)
                    st.rerun()
                except ApiError as e:
                    friendly_error(e)


# ---------- 任务 2：题目页面组 ----------

def page_problems():
    if "prob_view" not in st.session_state:
        st.session_state["prob_view"] = "list"
    view = st.session_state["prob_view"]
    if view == "detail":
        _problem_detail()
    elif view in ("new", "edit"):
        _problem_form()
    else:
        _problem_list()


def _problem_list():
    st.title("📋 题目列表")
    try:
        problems = api("GET", "/api/problems/")
    except ApiError as e:
        friendly_error(e)
        return
    st.caption(f"共 {len(problems)} 题")
    if st.button("➕ 新建题目", type="primary"):
        st.session_state["prob_view"] = "new"
        st.rerun()
    if not problems:
        st.info("暂无题目，点击上方按钮创建第一道题。")
        return
    st.dataframe(pd.DataFrame(problems), width="stretch", hide_index=True)
    sel = st.selectbox(
        "查看题目详情", [p["id"] for p in problems],
        format_func=lambda pid: f"{pid} · {next((p['title'] for p in problems if p['id'] == pid), '')}",
    )
    if st.button("打开详情"):
        st.session_state["prob_view"] = "detail"
        st.session_state["prob_id"] = sel
        st.session_state.pop("confirm_delete", None)
        st.rerun()


def _problem_detail():
    pid = st.session_state.get("prob_id")
    me = st.session_state.get("me")
    if st.button("← 返回列表"):
        st.session_state["prob_view"] = "list"
        st.session_state.pop("confirm_delete", None)
        st.rerun()
    try:
        p = api("GET", f"/api/problems/{pid}")
    except ApiError as e:
        friendly_error(e)
        return

    st.title(p["title"])
    meta = f"题目 ID：{p['id']} · 时间限制 {p.get('time_limit')}s · 内存限制 {p.get('memory_limit')}MB"
    if p.get("difficulty"):
        meta += f" · 难度 {p['difficulty']}"
    if p.get("author"):
        meta += f" · 作者 {p['author']}"
    if p.get("source"):
        meta += f" · 来源 {p['source']}"
    st.caption(meta)
    if p.get("tags"):
        st.markdown("　".join(f"`{t}`" for t in p["tags"]))

    tab_desc, tab_samples, tab_more = st.tabs(["题目描述", "样例", "数据范围 / 提示"])
    with tab_desc:
        st.markdown(p["description"] or "—")
        st.markdown("#### 输入格式")
        st.markdown(p["input_description"] or "—")
        st.markdown("#### 输出格式")
        st.markdown(p["output_description"] or "—")
    with tab_samples:
        for i, s in enumerate(p.get("samples") or []):
            c1, c2 = st.columns(2)
            c1.markdown(f"**样例 {i + 1} · 输入**")
            c1.code(s.get("input", ""))
            c2.markdown(f"**样例 {i + 1} · 输出**")
            c2.code(s.get("output", ""))
    with tab_more:
        st.markdown("#### 数据范围")
        st.markdown(p["constraints"] or "—")
        if p.get("hint"):
            st.markdown("#### 提示")
            st.markdown(p["hint"])

    c1, c2, c3, c4 = st.columns(4)
    if c1.button("✏️ 编辑题目", width="stretch"):
        st.session_state["prob_view"] = "edit"
        st.rerun()
    if c2.button("🚀 提交本题代码", width="stretch"):
        st.session_state["submit_problem"] = pid
        st.session_state["nav"] = "🚀 提交评测"
        st.rerun()
    if c3.button("📜 本题提交记录", width="stretch"):
        st.session_state["sub_filter_problem"] = pid
        st.session_state["nav"] = "📜 评测记录"
        st.rerun()
    if me.get("role") == "admin":
        if c4.button("🗑 删除题目", width="stretch"):
            st.session_state["confirm_delete"] = pid
        if st.session_state.get("confirm_delete") == pid:
            st.error(f"确认删除题目 {pid}？此操作不可恢复。")
            a, b = st.columns(2)
            if a.button("✅ 确认删除", width="stretch"):
                try:
                    api("DELETE", f"/api/problems/{pid}")
                    st.session_state.pop("confirm_delete", None)
                    st.session_state["prob_view"] = "list"
                    st.success("已删除。")
                    time.sleep(0.5)
                    st.rerun()
                except ApiError as e:
                    friendly_error(e)
            if b.button("取消", width="stretch"):
                st.session_state.pop("confirm_delete", None)
                st.rerun()


def _problem_form():
    is_edit = st.session_state["prob_view"] == "edit"
    pid = st.session_state.get("prob_id")
    p = {}
    if is_edit:
        try:
            p = api("GET", f"/api/problems/{pid}")
        except ApiError as e:
            friendly_error(e)
            return
    st.title("✏️ 编辑题目" if is_edit else "➕ 新建题目")
    if st.button("← 返回"):
        st.session_state["prob_view"] = "list" if not is_edit else "detail"
        st.rerun()

    with st.form("problem-form"):
        c1, c2 = st.columns(2)
        pid_in = c1.text_input("ID（唯一标识）", value=p.get("id", ""), disabled=is_edit)
        title = c1.text_input("标题", value=p.get("title", ""))
        difficulty = c2.text_input("难度（如：入门/简单/中等/困难）", value=p.get("difficulty", ""))
        author = c2.text_input("作者", value=p.get("author", ""))
        source = c2.text_input("来源", value=p.get("source", ""))
        tags = c2.text_input("标签（逗号分隔）", value=", ".join(p.get("tags") or []))
        c3, c4 = st.columns(2)
        time_limit = c3.number_input("时间限制（秒）", min_value=0.1, step=0.5,
                                     value=float(p.get("time_limit", 3.0)))
        memory_limit = c4.number_input("内存限制（MB）", min_value=1, step=1,
                                       value=int(p.get("memory_limit", 128)))
        description = st.text_area("题目描述（支持 Markdown）", value=p.get("description", ""), height=170)
        input_description = st.text_area("输入格式（支持 Markdown）", value=p.get("input_description", ""), height=110)
        output_description = st.text_area("输出格式（支持 Markdown）", value=p.get("output_description", ""), height=110)
        samples = st.text_area(
            "样例（JSON 数组：[{\"input\": \"...\", \"output\": \"...\"}]）",
            value=json.dumps(p.get("samples") or [{"input": "", "output": ""}], ensure_ascii=False, indent=2),
            height=140)
        constraints = st.text_area("数据范围（支持 Markdown）", value=p.get("constraints", ""), height=100)
        testcases = st.text_area(
            "测试点（JSON 数组：[{\"input\": \"...\", \"output\": \"...\"}]）",
            value=json.dumps(p.get("testcases") or [{"input": "", "output": ""}], ensure_ascii=False, indent=2),
            height=140)
        hint = st.text_input("提示（可选）", value=p.get("hint", ""))
        submitted = st.form_submit_button("💾 保存", width="stretch")

    if not submitted:
        return
    # —— 提交前格式检查（任务 2）——
    errors = []
    if not pid_in.strip():
        errors.append("ID 不能为空。")
    if not title.strip():
        errors.append("标题不能为空。")
    for name, value in (("题目描述", description), ("输入格式", input_description),
                        ("输出格式", output_description), ("数据范围", constraints)):
        if not value.strip():
            errors.append(f"{name}不能为空。")
    sample_list = case_list = None
    try:
        sample_list = json.loads(samples)
        if not isinstance(sample_list, list) or any(
                not isinstance(s, dict) or not isinstance(s.get("input"), str)
                or not isinstance(s.get("output"), str) for s in sample_list):
            raise ValueError
    except (ValueError, json.JSONDecodeError):
        errors.append("样例必须是 JSON 数组，每项含字符串 input/output。")
    try:
        case_list = json.loads(testcases)
        if not isinstance(case_list, list) or any(
                not isinstance(c, dict) or not isinstance(c.get("input"), str)
                or not isinstance(c.get("output"), str) for c in case_list):
            raise ValueError
    except (ValueError, json.JSONDecodeError):
        errors.append("测试点必须是 JSON 数组，每项含字符串 input/output。")
    if errors:
        for e in errors:
            st.error(e)
        return

    body = {
        "id": pid_in.strip(),
        "title": title.strip(),
        "description": description,
        "input_description": input_description,
        "output_description": output_description,
        "samples": sample_list,
        "constraints": constraints,
        "testcases": case_list,
        "hint": hint,
        "source": source,
        "tags": [t.strip() for t in tags.split(",") if t.strip()],
        "time_limit": float(time_limit),
        "memory_limit": int(memory_limit),
        "author": author,
        "difficulty": difficulty,
    }
    try:
        if is_edit:
            api("PUT", f"/api/problems/{pid}", json=body)
            st.success("已保存修改。")
        else:
            api("POST", "/api/problems/", json=body)
            st.success("已创建题目。")
    except ApiError as e:
        friendly_error(e)
        return
    st.session_state["prob_id"] = pid_in.strip()
    st.session_state["prob_view"] = "detail"
    time.sleep(0.5)
    st.rerun()


# ---------- 任务 3：评测与提交页面组 ----------

def page_submit():
    st.title("🚀 提交评测")
    try:
        problems = api("GET", "/api/problems/")
        langs = (api("GET", "/api/languages/") or {}).get("name") or []
    except ApiError as e:
        friendly_error(e)
        return
    if not problems:
        st.info("暂无题目。")
        return
    if not langs:
        st.info("暂无可用语言，请联系管理员注册。")
        return
    preset = st.session_state.pop("submit_problem", None)
    index = next((i for i, pr in enumerate(problems) if pr["id"] == preset), 0)
    pid = st.selectbox("题目", [p["id"] for p in problems], index=index,
                       format_func=lambda x: f"{x} · {next((p['title'] for p in problems if p['id'] == x), '')}")
    lang = st.selectbox("语言", langs)
    code = st.text_area("代码", height=320, placeholder="在此粘贴你的代码")
    if st.button("提交评测", type="primary"):
        if not code.strip():
            st.error("代码不能为空。")
            return
        try:
            resp = api("POST", "/api/submissions/",
                       json={"problem_id": pid, "language": lang, "code": code})
        except ApiError as e:
            friendly_error(e)
            return
        sid = resp["submission_id"]
        st.success(f"提交成功，评测 ID #{sid}，等待评测…")
        _poll_submission(sid, show_log=True)


def page_submissions():
    if "sub_view" not in st.session_state:
        st.session_state["sub_view"] = "list"
    if st.session_state["sub_view"] == "detail":
        _submission_detail()
    else:
        _submission_list()


def _submission_list():
    me = st.session_state.get("me")
    st.title("📜 评测记录")
    preset_problem = st.session_state.pop("sub_filter_problem", "")
    c1, c2 = st.columns(2)
    status = c1.selectbox("状态", ["全部", "pending", "success", "error"],
                          format_func=lambda x: {"全部": "全部", "pending": "等待中",
                                                 "success": "通过", "error": "未通过"}[x])
    problem = c2.text_input("题目 ID（可选）", value=preset_problem)
    user_id = None
    if me.get("role") == "admin":
        user_id = st.text_input("用户 ID（可选，留空查全部）")
    params = {}
    if status != "全部":
        params["status"] = status
    if problem.strip():
        params["problem_id"] = problem.strip()
    if me.get("role") != "admin":
        params["user_id"] = me["user_id"]
    elif user_id and user_id.strip():
        if not user_id.strip().isdigit():
            st.error("用户 ID 必须是数字。")
            return
        params["user_id"] = int(user_id.strip())
    try:
        data = api("GET", "/api/submissions/", params=params or None)
    except ApiError as e:
        friendly_error(e)
        return
    subs = data.get("submissions", [])
    st.caption(f"共 {data.get('total', 0)} 条")
    if not subs:
        st.info("暂无提交记录。")
        return
    st.dataframe(pd.DataFrame([
        {"ID": s.get("submission_id"), "题目": s.get("problem_id", "—"),
         "用户": s.get("user_id", "—"), "语言": s.get("language", "—"),
         "状态": STATUS_TEXT.get(s.get("status"), s.get("status", "—")).replace("✅ ", "").replace("❌ ", "").replace("⏳ ", ""),
         "得分": s.get("score", "—"), "时间": s.get("submit_time", "—")}
        for s in subs
    ]), width="stretch", hide_index=True)
    sel = st.selectbox("查看提交详情", [s["submission_id"] for s in subs],
                       format_func=lambda x: f"#{x}")
    if st.button("打开详情"):
        st.session_state["sub_view"] = "detail"
        st.session_state["sub_id"] = sel
        st.rerun()


def _submission_detail():
    sid = st.session_state.get("sub_id")
    me = st.session_state.get("me")
    if st.button("← 返回列表"):
        st.session_state["sub_view"] = "list"
        st.rerun()
    try:
        s = api("GET", f"/api/submissions/{sid}")
    except ApiError as e:
        friendly_error(e)
        return
    if s.get("status") == "pending":
        # 任务 3：轮询 submission_id 的评测状态与结果
        for _ in range(60):
            time.sleep(1)
            try:
                s = api("GET", f"/api/submissions/{sid}")
            except ApiError as e:
                friendly_error(e)
                return
            if s.get("status") != "pending":
                break
        else:
            st.warning("等待超时，可稍后点击按钮刷新。")
    _render_submission(s, show_log=True)
    if s.get("status") == "pending" and st.button("🔄 手动刷新"):
        st.rerun()
    if me.get("role") == "admin" and st.button("♻️ 重新评测"):
        try:
            api("PUT", f"/api/submissions/{sid}/rejudge")
            st.success("已重新评测。")
            time.sleep(0.5)
            st.rerun()
        except ApiError as e:
            friendly_error(e)


def _poll_submission(sid: str, show_log: bool):
    """提交后的即时轮询展示：占位容器内实时更新，直到评测完成。"""
    ph = st.empty()
    for _ in range(60):
        time.sleep(1)
        try:
            s = api("GET", f"/api/submissions/{sid}")
        except ApiError as e:
            with ph.container():
                friendly_error(e)
            return
        with ph.container():
            _render_submission(s, show_log=show_log)
        if s.get("status") != "pending":
            return
    with ph.container():
        st.warning("等待超时，可稍后到「评测记录」查看结果。")


def _render_submission(s: dict, show_log: bool):
    status = s.get("status", "pending")
    st.markdown(f"### 提交 #{s.get('submission_id')} — {STATUS_TEXT.get(status, status)}")
    meta = " · ".join(x for x in (
        f"题目 {s.get('problem_id', '—')}", f"用户 {s.get('user_id', '—')}",
        f"语言 {s.get('language', '—')}", f"提交于 {s.get('submit_time', '—')}") if x)
    st.caption(meta)
    if status == "pending":
        st.info("⏳ 正在评测，请稍候…")
        return
    score = s.get("score")
    counts = s.get("counts") or {}
    c1, c2 = st.columns(2)
    c1.metric("得分", score if score is not None else "—")
    c2.metric("测试点", sum(counts.values()))
    if counts:
        st.markdown("　".join(
            f"`{k}` × {v} {VERDICT_TEXT.get(k, '')}" for k, v in counts.items()))
    # CE / RE / TLE 等错误明确展示（任务 3）
    if s.get("compile_info"):
        st.markdown("#### 编译信息（编译错误）")
        st.code(s["compile_info"])
    if s.get("run_info"):
        st.markdown("#### 运行信息")
        st.code(s["run_info"])
    if s.get("error_info"):
        st.markdown("#### 错误信息")
        st.code(s["error_info"])
    if s.get("code"):
        with st.expander("查看提交代码"):
            st.code(s["code"], language=s.get("language"))
    if show_log:
        try:
            log = api("GET", f"/api/submissions/{s['submission_id']}/log")
        except ApiError:
            log = None
        if log is not None:
            details = log.get("details")
            if details:
                st.markdown("#### 测试点明细")
                st.dataframe(pd.DataFrame(details), width="stretch", hide_index=True)
            elif not details and status in ("success", "error"):
                st.caption("该题测试点未公开，暂无明细。")


# ---------- 入口 ----------

def main():
    page = render_sidebar()
    if page == "🔑 登录":
        page_login()
    elif page == "📝 注册":
        page_register()
    elif page == "📋 题目":
        page_problems()
    elif page == "🚀 提交评测":
        page_submit()
    elif page == "📜 评测记录":
        page_submissions()
    elif page == "🙍 个人主页":
        page_profile()
    elif page == "🛠 用户管理":
        page_admin_users()


main()
