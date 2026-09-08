"""Step 6 前端：Streamlit 页面，通过 REST API 对接 FastAPI 后端（api.md 接口标准）。

运行：streamlit run app.py（后端需已在 8000 端口运行，可用 OJ_API_BASE 环境变量指向其它地址）

要求（step6.md）：
- Step 2 扩展入口：查询语言列表、登录用户动态注册语言
- 任务 1 用户页面组：注册/登录/退出、用户信息展示、用户管理（仅管理员）
- 任务 2 题目页面组：列表/详情/新增/编辑/删除，表单提交前做格式检查
- 任务 3 评测与提交页面组：题目详情内嵌代码提交（力扣式双栏）、提交记录列表/详情、轮询评测状态、明确展示 CE/RE/TLE 等
- 任务 4 接口对接：统一 API 封装、身份存 session_state（会话 Cookie 经 httpx 传递）、
  不硬编码用户身份、按响应 code/msg 展示成败、不绕过后端直接读写数据
"""

import html
import json
import os
import re
import time
from urllib.parse import urlsplit

import httpx
import pandas as pd
import streamlit as st

from app import config
from app.core.deps import SESSION_COOKIE
from app.frontend_cookie import BROWSER_SESSION_COOKIE, write_session_cookie

API_BASE = os.environ.get("OJ_API_BASE", "http://127.0.0.1:8000")

st.set_page_config(page_title="Online Judge", page_icon="⚖️", layout="wide")

STATUS_TEXT = {"pending": "⏳ 等待评测", "success": "✅ 评测完成", "error": "❌ 评测失败"}
VERDICT_TEXT = {
    "AC": "通过", "WA": "答案错误", "TLE": "超出时间限制",
    "MLE": "超出内存限制", "RE": "运行时错误", "CE": "编译错误",
}
ROLE_TEXT = {"user": "用户", "admin": "管理员", "banned": "封禁"}
ACCESS_TEXT = {"200": "✅ 允许", "401": "🚫 未登录", "403": "⛔ 拒绝"}
AI_MODE_LABELS = {
    "fast": "⚡ 极速｜Flash · 关闭思考",
    "balanced": "⚖️ 均衡｜Flash · 低推理强度",
    "quality": "🎯 高质量｜Pro · 高推理强度",
}


# ---------- 统一 API 封装（任务 4） ----------

class ApiError(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code = code
        self.msg = msg


_SESSION_PARAM = "oj_s"   # 仅用于清理旧版 URL 凭据
_UID_PARAM = "oj_u"

# main() 在每次渲染时注册的 Streamlit 原生页面。所有内部跳转都通过
# st.page_link / st.switch_page 完成，由 Streamlit 统一管理 URL、浏览器历史和会话。
_ACTIVE_PAGES: dict[str, object] = {}


def _valid_problem_id(value: object) -> bool:
    return bool(re.fullmatch(r"[0-9]{1,18}", str(value)))


def _supports_deepseek_modes(provider_url: object) -> bool:
    try:
        return (urlsplit(str(provider_url)).hostname or "").lower() == "api.deepseek.com"
    except ValueError:
        return False


def _go(page: str, **params) -> None:
    """在当前标签页中进入已注册的 Streamlit 页面。"""
    query_params = {key: str(value) for key, value in params.items()
                    if value not in (None, "")}
    st.switch_page(_ACTIVE_PAGES[page], query_params=query_params)


def _clear_session(*, clear_browser_cookie: bool = True):
    """清除本地登录态与页面状态（登出/会话过期时），并清掉 URL 中的会话参数。"""
    for key in ("me", "cookies", "prob_view", "prob_id",
                "sub_view", "sub_id", "confirm_delete",
                "ai_view", "ai_task_id", "audit_user", "audit_username",
                "ai_retry_notice", "ai_refine_notice", "audit_problem", "audit_page",
                "audit_confirm_delete", "audit_flash"):
        st.session_state.pop(key, None)
    st.session_state.pop("_session_restore_error", None)
    if clear_browser_cookie:
        st.session_state["_clear_browser_session_cookie"] = True
    try:
        for k in (_SESSION_PARAM, _UID_PARAM):
            st.query_params.pop(k, None)
    except Exception:
        pass   # 环境不支持 query_params 时静默降级


def _persist_login():
    """安排下次渲染同步浏览器 Cookie，并清除旧版 URL 凭据。"""
    st.session_state.pop("_clear_browser_session_cookie", None)
    st.session_state.pop("_session_restore_error", None)
    for key in (_SESSION_PARAM, _UID_PARAM):
        st.query_params.pop(key, None)


def _restore_login() -> str:
    """整页刷新后用浏览器 Cookie 向后端校验并恢复登录用户。"""
    for key in (_SESSION_PARAM, _UID_PARAM):
        st.query_params.pop(key, None)
    if st.session_state.get("me"):
        return "authenticated"
    try:
        # 前端持久化 Cookie 使用独立名称，避免与后端可能下发的
        # HttpOnly oj_session 同名冲突；第二项仅用于平滑兼容旧版。
        token = (
            st.context.cookies.get(BROWSER_SESSION_COOKIE)
            or st.context.cookies.get(SESSION_COOKIE)
        )
    except Exception:
        token = None
    if not isinstance(token, str) or not token:
        return "guest"
    st.session_state["cookies"] = {SESSION_COOKIE: token}
    try:
        st.session_state["me"] = api("GET", "/api/auth/me", timeout=5)
    except ApiError as err:
        if err.code in (401, 403):
            # 401 时 api() 已清理会话；403 表示账号已不允许恢复。
            if err.code == 403:
                _clear_session()
            return "guest"
        # 连接、5xx 或非预期响应都不等于会话失效：保留 Cookie
        # 和当前 URL，后端恢复后可原地继续。
        st.session_state["_session_restore_error"] = err.msg
        return "unavailable"
    st.session_state.pop("_session_restore_error", None)
    return "authenticated"


def api(method: str, path: str, **kwargs):
    """统一 API 调用：自动携带会话 Cookie，解析 {code, msg, data} 协议。

    - 成功返回 data；失败抛 ApiError（code/msg 与后端一致）
    - 401 视为会话失效，自动清除本地登录态
    - 后端不可达抛 ApiError(0, …)
    """
    cookies = st.session_state.get("cookies", {})
    request_timeout = kwargs.pop("timeout", 30)
    try:
        # 沙箱/开发机常设置 HTTP_PROXY，而部分客户端不识别 NO_PROXY 中的
        # `127.*` 写法；后端是本机服务，必须直连，避免请求被代理成 502。
        resp = httpx.request(
            method, API_BASE + path, cookies=cookies, timeout=request_timeout,
            trust_env=False, **kwargs,
        )
    except httpx.HTTPError as e:
        raise ApiError(0, f"无法连接后端（{API_BASE}）：{e}") from e
    try:
        body = resp.json()
    except ValueError:
        raise ApiError(resp.status_code, f"后端返回非 JSON（HTTP {resp.status_code}）") from None
    st.session_state["cookies"] = dict(resp.cookies) or cookies   # 登录/登出会下发新 Cookie
    if resp.status_code != 200 or body.get("code") != 200:
        if body.get("code") == 401:
            _clear_session()
        raise ApiError(resp.status_code if resp.status_code != 200 else body.get("code"),
                       body.get("msg", f"HTTP {resp.status_code}"))
    return body.get("data")


def friendly_error(err: ApiError):
    """失败友好提示：401 提示登录、429 提示限流，其余展示后端 msg。"""
    if err.code == 401:
        st.warning("尚未登录或会话已过期，请先在左侧登录。")
    elif err.code == 429:
        st.error("操作过于频繁，请稍后再试。")
    else:
        st.error(f"操作失败（{err.code}）：{err.msg}")


# ---------- UI 主题（洛谷 × 力扣风格） ----------

_UI_CSS = """
<style>
:root {
  --oj-blue: #2563eb;
  --oj-indigo: #4f46e5;
  --oj-cyan: #06b6d4;
  --oj-teal: #14b8a6;
  --oj-violet: #8b5cf6;
  --oj-ink: #172033;
  --oj-muted: #667085;
  --oj-line: rgba(148, 163, 184, .24);
  --oj-shadow: 0 18px 50px rgba(43, 67, 101, .11);
}
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
[data-testid="stAppDeployButton"] {display: none;}
header[data-testid="stHeader"] {background: transparent; pointer-events: none;}

/* 多层渐变、细网格和柔光构成页面背景。 */
[data-testid="stApp"] {
  background:
    radial-gradient(900px 560px at 96% -8%, rgba(139, 92, 246, .18), transparent 64%) fixed,
    radial-gradient(760px 520px at -8% 22%, rgba(6, 182, 212, .16), transparent 65%) fixed,
    radial-gradient(680px 460px at 76% 106%, rgba(251, 146, 60, .12), transparent 66%) fixed,
    linear-gradient(rgba(99, 102, 241, .035) 1px, transparent 1px) 0 0 / 32px 32px fixed,
    linear-gradient(90deg, rgba(99, 102, 241, .035) 1px, transparent 1px) 0 0 / 32px 32px fixed,
    linear-gradient(145deg, #eef6ff 0%, #f7f5ff 46%, #eefbf9 100%) fixed;
}
.block-container {
  max-width: 1210px; margin-top: 1.25rem; margin-bottom: 2.2rem; padding: 2.35rem 2.7rem 3rem;
  background: linear-gradient(145deg, rgba(255,255,255,.93), rgba(255,255,255,.78));
  border: 1px solid rgba(255,255,255,.82); border-radius: 24px;
  box-shadow: var(--oj-shadow), inset 0 1px 0 rgba(255,255,255,.9); backdrop-filter: blur(16px);
}

/* 标题与正文。 */
h1 {font-weight: 850; letter-spacing: -.025em; color: var(--oj-ink); padding-bottom: 7px;}
h1::after {content: ""; display: block; width: 72px; height: 5px; margin-top: 10px;
  border-radius: 999px; background: linear-gradient(90deg, var(--oj-blue), var(--oj-violet), var(--oj-teal));
  box-shadow: 0 3px 12px rgba(79, 70, 229, .25);}
h2, h3 {color: var(--oj-ink); letter-spacing: -.012em;}
h3 {border-left: 4px solid #60a5fa; padding-left: 10px;}
a {color: var(--oj-blue);}
[data-testid="stCaptionContainer"] {color: var(--oj-muted);}
hr {border-color: var(--oj-line); margin: 1.45rem 0;}

/* 深色渐变侧边栏与彩色品牌区。 */
[data-testid="stSidebar"] {
  border-right: 0;
  background:
    radial-gradient(260px 220px at 18% 4%, rgba(34, 211, 238, .23), transparent 68%),
    radial-gradient(280px 240px at 108% 78%, rgba(167, 139, 250, .24), transparent 70%),
    linear-gradient(165deg, #15284d 0%, #1e3a6d 48%, #29245b 100%);
  box-shadow: 10px 0 34px rgba(23, 39, 73, .18);
}
[data-testid="stSidebar"]::before {content: ""; display: block; height: 5px;
  background: linear-gradient(90deg, #22d3ee, #60a5fa 38%, #a78bfa 72%, #fb7185);}
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] {color: rgba(241, 245, 249, .82);}
[data-testid="stSidebar"] hr {border-color: rgba(255,255,255,.13);}
[data-testid="stSidebar"] [data-testid="stPageLink"] a {
  padding: 10px 13px; border: 1px solid transparent; border-radius: 11px; margin: 4px 0;
  color: rgba(248,250,252,.86); text-decoration: none;
  transition: background .18s, border-color .18s, transform .18s;}
[data-testid="stSidebar"] [data-testid="stPageLink"] a:hover {
  background: rgba(255,255,255,.09); border-color: rgba(255,255,255,.12); transform: translateX(3px);}
[data-testid="stSidebar"] [data-testid="stPageLink"] a[aria-current="page"] {
  background: linear-gradient(100deg, rgba(56,189,248,.25), rgba(139,92,246,.22));
  border-color: rgba(125,211,252,.34); color: #fff; font-weight: 750;
  box-shadow: 0 8px 24px rgba(2, 8, 23, .15);}
[data-testid="stSidebar"] button {
  background: rgba(255,255,255,.08); border-color: rgba(255,255,255,.2); color: #f8fafc;}
[data-testid="stSidebar"] button:hover {background: rgba(255,255,255,.15); border-color: rgba(255,255,255,.34);}
.oj-brand {display:flex; align-items:center; gap:12px; margin: 4px 0 10px;}
.oj-brand-mark {display:grid; place-items:center; width:44px; height:44px; border-radius:14px;
  color:#fff; font-size:22px; background:linear-gradient(145deg,#22d3ee,#6366f1 62%,#a855f7);
  box-shadow:0 8px 22px rgba(34,211,238,.24), inset 0 1px 0 rgba(255,255,255,.32);}
.oj-brand-name {color:#fff; font-size:1.08rem; line-height:1.15; font-weight:800; letter-spacing:.01em;}
.oj-brand-sub {color:rgba(226,232,240,.65); font-size:.72rem; margin-top:4px; letter-spacing:.08em; text-transform:uppercase;}
.oj-user-card {padding:10px 12px; margin:2px 0 4px; border-radius:12px;
  color:#eaf2ff; background:rgba(255,255,255,.075); border:1px solid rgba(255,255,255,.11);}

/* 按钮和表单。 */
.stButton > button, [data-testid="stBaseButton-primary"], button[kind] {
  border-radius: 10px; font-weight: 680; transition: transform .16s, box-shadow .16s, border-color .16s;}
.stButton > button:hover, button[kind]:hover {transform: translateY(-1px); box-shadow: 0 7px 18px rgba(51,65,85,.12);}
[data-testid="stBaseButton-primary"], button[kind="primary"] {
  background: linear-gradient(120deg, var(--oj-blue), var(--oj-indigo) 58%, var(--oj-violet));
  color: #fff; border: none; box-shadow: 0 7px 20px rgba(79,70,229,.22);}
[data-testid="stBaseButton-primary"]:hover, button[kind="primary"]:hover {
  background: linear-gradient(120deg, #1d4ed8, #4338ca 58%, #7c3aed); box-shadow: 0 10px 24px rgba(79,70,229,.3);}
[data-testid="stForm"] {
  background: linear-gradient(145deg, rgba(239,246,255,.78), rgba(250,245,255,.76));
  border: 1px solid rgba(129,140,248,.2); border-radius: 17px; padding: 1.35rem 1.45rem;
  box-shadow: 0 10px 28px rgba(71,85,105,.07);}
[data-baseweb="input"] > div, [data-baseweb="select"] > div,
[data-testid="stTextArea"] textarea, [data-testid="stNumberInput"] input {
  background: rgba(255,255,255,.9); border-color: rgba(148,163,184,.36); border-radius: 9px;}
[data-baseweb="input"] > div:focus-within, [data-baseweb="select"] > div:focus-within,
[data-testid="stTextArea"] textarea:focus {border-color: #60a5fa; box-shadow: 0 0 0 3px rgba(96,165,250,.16);}

/* 指标卡使用多彩顶部光带。 */
[data-testid="stMetric"] {background: linear-gradient(145deg, #fff, #f8fbff); border: 1px solid var(--oj-line);
  border-radius: 15px; padding: 16px 19px; position: relative; overflow: hidden;
  box-shadow: 0 8px 24px rgba(71,85,105,.07); transition: box-shadow .2s, transform .2s;}
[data-testid="stMetric"]::before {content: ""; position: absolute; top: 0; left: 0; right: 0;
  height: 4px; background: linear-gradient(90deg, var(--oj-cyan), var(--oj-blue), var(--oj-violet));}
[data-testid="column"]:nth-child(2n) [data-testid="stMetric"]::before {
  background: linear-gradient(90deg, #8b5cf6, #ec4899, #fb7185);}
[data-testid="column"]:nth-child(3n) [data-testid="stMetric"]::before {
  background: linear-gradient(90deg, #14b8a6, #22c55e, #84cc16);}
[data-testid="stMetric"]:hover {box-shadow: 0 14px 30px rgba(51,65,85,.13); transform: translateY(-3px);}
[data-testid="stMetricValue"] {color: var(--oj-ink); font-weight: 800;}
[data-testid="stMetricLabel"] {color: var(--oj-muted);}

/* 标签页、折叠面板、进度条和代码块。 */
[data-testid="stTabs"] [role="tablist"] {gap:6px; border-bottom-color:var(--oj-line);}
button[role="tab"] {border-radius: 9px 9px 0 0; padding-left:14px; padding-right:14px;}
button[role="tab"][aria-selected="true"] {color: var(--oj-indigo); font-weight: 750; background:rgba(99,102,241,.08);}
[data-testid="stExpander"] {background: rgba(255,255,255,.74); border:1px solid var(--oj-line);
  border-radius:13px; box-shadow:0 5px 18px rgba(71,85,105,.05); overflow:hidden;}
[data-testid="stCodeBlock"] pre {background: linear-gradient(145deg,#172033,#202c46) !important;
  border:1px solid rgba(125,211,252,.15); border-radius: 12px; box-shadow:0 10px 25px rgba(15,23,42,.16);}
[data-testid="stProgress"] > div > div > div > div {
  background: linear-gradient(90deg, var(--oj-cyan), var(--oj-blue), var(--oj-violet));}

/* 提示框与数据表。 */
[data-testid="stAlert"] {border-radius: 12px; border-left: 5px solid #94a3b8;
  box-shadow: 0 6px 18px rgba(71,85,105,.06);}
[data-testid="stAlert"][kind="success"] {border-left-color: #22c55e;}
[data-testid="stAlert"][kind="info"] {border-left-color: #3b82f6;}
[data-testid="stAlert"][kind="warning"] {border-left-color: #f59e0b;}
[data-testid="stAlert"][kind="error"] {border-left-color: #ef4444;}
[data-testid="stDataFrame"] {border: 1px solid var(--oj-line); border-radius: 13px; overflow: hidden;
  box-shadow:0 8px 22px rgba(71,85,105,.06);}

/* 自定义表格：渐变表头、斑马纹与悬浮高亮。 */
.oj-card {border: 1px solid var(--oj-line); border-radius: 15px; overflow: hidden;
  background: rgba(255,255,255,.9); box-shadow: 0 9px 26px rgba(71,85,105,.08); margin: .45rem 0 1.15rem;
  transition: box-shadow .2s, transform .2s;}
.oj-card:hover {box-shadow: 0 16px 34px rgba(51,65,85,.13); transform: translateY(-2px);}
.oj-table {width: 100%; border-collapse: collapse; font-size: 14px;}
.oj-table thead th {background: linear-gradient(110deg, #edf6ff, #f1efff 65%, #ecfdf8);
  color: #3f4b63; text-align: left; font-weight: 750; padding: 12px 14px; border-bottom: 1px solid rgba(99,102,241,.16);}
.oj-table tbody td {padding: 11px 14px; border-bottom: 1px solid rgba(226,232,240,.78); color: var(--oj-ink);}
.oj-table tbody tr:nth-child(even) {background: rgba(241,245,249,.48);}
.oj-table tbody tr:hover {background: linear-gradient(90deg, rgba(219,234,254,.64), rgba(237,233,254,.48));}
.oj-table tbody tr:last-child td {border-bottom: none;}
.oj-mono {font-family: ui-monospace, SFMono-Regular, Menlo, monospace; color: #4f5d75;}
/* 登录/注册页的彩色横幅。 */
.oj-hero {position:relative; overflow:hidden; min-height:150px; display:flex; align-items:center;
  padding:30px 36px; margin:0 0 1.3rem; border-radius:20px; color:#fff;
  background:linear-gradient(125deg,#0ea5e9 0%,#4f46e5 46%,#8b5cf6 75%,#ec4899 115%);
  box-shadow:0 18px 38px rgba(79,70,229,.28); isolation:isolate;}
.oj-hero::before {content:""; position:absolute; inset:0;
  background:linear-gradient(110deg,rgba(255,255,255,.13),transparent 45%,rgba(255,255,255,.07)); z-index:-1;}
.oj-hero-copy {position:relative; z-index:2;}
.oj-hero-kicker {display:inline-block; padding:4px 10px; margin-bottom:9px; border-radius:999px;
  background:rgba(255,255,255,.16); border:1px solid rgba(255,255,255,.22); font-size:.72rem;
  font-weight:700; letter-spacing:.12em; text-transform:uppercase;}
.oj-hero-title {font-size:1.72rem; font-weight:850; letter-spacing:-.02em;}
.oj-hero-sub {opacity:.88; margin-top:5px;}
.oj-orb {position:absolute; border-radius:50%; background:rgba(255,255,255,.11); border:1px solid rgba(255,255,255,.12);}
.oj-orb.one {width:230px;height:230px;right:65px;top:-105px;}
.oj-orb.two {width:125px;height:125px;right:205px;bottom:-70px;}
.oj-orb.three {width:66px;height:66px;right:24px;bottom:18px;}
.oj-code-mark {position:absolute; right:78px; top:47px; color:rgba(255,255,255,.78);
  font:800 2.2rem/1 ui-monospace,monospace; letter-spacing:.1em; transform:rotate(-5deg);}

@media (max-width: 760px) {
  .block-container {margin-top:.5rem; padding:1.4rem 1rem 2rem; border-radius:16px;}
  .oj-hero {padding:24px 22px; min-height:130px;}
  .oj-code-mark {display:none;}
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {scroll-behavior:auto !important; transition:none !important;}
}
</style>
"""

_HERO = """
<div class="oj-hero">
  <span class="oj-orb one"></span>
  <span class="oj-orb two"></span>
  <span class="oj-orb three"></span>
  <span class="oj-code-mark">&lt;/&gt;</span>
  <div class="oj-hero-copy">
    <div class="oj-hero-kicker">Code · Judge · Improve</div>
    <div class="oj-hero-title">⚖️ Online Judge</div>
  </div>
</div>
"""

# 徽章配色（力扣式圆角胶囊）：绿=通过 / 红=未通过 / 黄=等待 / 蓝=信息 / 灰=中性
_BADGE_COLORS = {
    "green": ("#e6f6ec", "#1a7f4b"),
    "red": ("#fdecec", "#c0392b"),
    "amber": ("#fff4e0", "#b7791f"),
    "blue": ("#e8f1fb", "#2b6cb0"),
    "teal": ("#e0f5f1", "#0d9488"),
    "gray": ("#eef1f4", "#57606a"),
}


def _badge(text: str, kind: str = "gray") -> str:
    """力扣式状态胶囊（HTML inline-block）。"""
    bg, fg = _BADGE_COLORS.get(kind, _BADGE_COLORS["gray"])
    return (f'<span style="display:inline-block;padding:2px 10px;border-radius:999px;'
            f'background:{bg};color:{fg};font-size:12px;font-weight:600;'
            f'white-space:nowrap">{html.escape(str(text))}</span>')


def _html_table(headers: list[str], rows: list[str]) -> str:
    """洛谷题单风格 HTML 表格（rows 每项为 `<td>…</td>` 拼接的 `<tr>`）。"""
    thead = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    return (f'<div class="oj-card"><table class="oj-table">'
            f'<thead><tr>{thead}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


def _diff_badge(diff: str) -> str:
    """难度徽章（力扣配色）：简单/入门→青绿、中等→金黄、困难→红。"""
    d = (diff or "").strip()
    if not d:
        return "—"
    if any(k in d for k in ("简", "入", "易", "easy")):
        return _badge(d, "teal")
    if any(k in d for k in ("难", "困", "hard")):
        return _badge(d, "red")
    return _badge(d, "amber")


def _status_badge(status) -> str:
    s = str(status or "pending")
    return {"success": _badge(STATUS_TEXT["success"], "green"), "error": _badge(STATUS_TEXT["error"], "red"),
            "pending": _badge(STATUS_TEXT["pending"], "amber")}.get(s, _badge(s, "gray"))


def _role_badge(role) -> str:
    r = str(role or "user")
    return {"admin": _badge("管理员", "blue"), "banned": _badge("封禁", "red"),
            "user": _badge("用户", "gray")}.get(r, _badge(r, "gray"))


def _access_badge(status) -> str:
    s = str(status)
    return {"200": _badge(ACCESS_TEXT["200"], "green"), "401": _badge(ACCESS_TEXT["401"], "amber"),
            "403": _badge(ACCESS_TEXT["403"], "red")}.get(s, _badge(s, "gray"))


def _ai_status_badge(status) -> str:
    s = str(status or "pending")
    return {"done": _badge("完成", "green"), "failed": _badge("失败", "red"),
            "running": _badge("执行中", "blue"), "cancelled": _badge("已中断", "gray"),
            "pending": _badge("等待中", "amber")}.get(s, _badge(s, "gray"))


def _ai_mode_radio(*, current: str | None, disabled: bool, key: str):
    options = list(AI_MODE_LABELS)
    index = options.index(current) if current in options else 1
    return st.radio(
        "生成模式",
        options,
        index=index,
        format_func=AI_MODE_LABELS.get,
        horizontal=True,
        disabled=disabled,
        key=key,
        help="DeepSeek 官方接口支持：极速适合常规题，均衡兼顾速度与推理，高质量适合困难题。",
    )


def _verdict_pill(result: str, count) -> str:
    """测试点统计彩色胶囊（AC 绿 / WA·RE·CE 红 / TLE·MLE 黄）。"""
    r = str(result or "UNK").upper()
    kind = {"AC": "green", "WA": "red", "RE": "red", "CE": "red",
            "TLE": "amber", "MLE": "amber"}.get(r, "gray")
    text = f"{r} × {count} {VERDICT_TEXT.get(r, '')}"
    return _badge(text, kind)


def _inject_ui():
    """注入全局主题 CSS（每页开头调用一次）。"""
    st.markdown(_UI_CSS, unsafe_allow_html=True)


# ---------- 侧边栏：登录态 + Streamlit 原生导航 ----------

_PAGE_LABELS = {
    "login": ("🔑", "登录"),
    "register": ("📝", "注册"),
    "problems": ("📋", "题目"),
    "submissions": ("📜", "评测记录"),
    "languages": ("🧩", "语言管理"),
    "ai": ("✨", "AI 命题"),
    "profile": ("🙍", "个人主页"),
    "users": ("🛠️", "用户管理"),
    "audit": ("🛡️", "访问审计"),
    "problem_new": ("➕", "新建题目"),
    "problem_detail": ("📖", "题目详情"),
    "problem_edit": ("✏️", "编辑题目"),
    "submission_detail": ("📄", "提交详情"),
    "ai_task": ("🤖", "AI 任务详情"),
}

_SIDEBAR_PAGE_KEYS = (
    "problems", "submissions", "languages", "ai", "profile", "users", "audit",
)


def render_sidebar() -> None:
    me = st.session_state.get("me")
    with st.sidebar:
        st.markdown(
            '<div class="oj-brand"><div class="oj-brand-mark">⚖</div>'
            '<div><div class="oj-brand-name">Online Judge</div>'
            '<div class="oj-brand-sub">Learn · Code · Grow</div></div></div>',
            unsafe_allow_html=True,
        )
        st.divider()
        if me:
            role = "🔑 管理员" if me.get("role") == "admin" else "👤 用户"
            st.markdown(
                f'<div class="oj-user-card"><strong>{html.escape(str(me.get("username", "")))}</strong>'
                f'<br><span style="font-size:.78rem;opacity:.72">{role}</span></div>',
                unsafe_allow_html=True,
            )
            st.divider()
            for key in _SIDEBAR_PAGE_KEYS:
                if key not in _ACTIVE_PAGES:
                    continue
                page = _ACTIVE_PAGES[key]
                icon, label = _PAGE_LABELS[key]
                st.page_link(page, label=label, icon=icon, width="stretch")
            st.divider()
            if st.button("退出登录", width="stretch"):
                try:
                    api("POST", "/api/auth/logout")
                except ApiError:
                    pass   # 会话已失效也无妨
                _clear_session()
                st.rerun()
        else:
            st.markdown("尚未登录，请先登录后使用。")
            for key in ("login", "register"):
                page = _ACTIVE_PAGES[key]
                icon, label = _PAGE_LABELS[key]
                st.page_link(page, label=label, icon=icon, width="stretch")


# ---------- 任务 1：用户页面组 ----------

def page_login():
    st.markdown(_HERO, unsafe_allow_html=True)
    st.title("🔑 登录")
    with st.form("login-form"):
        username = st.text_input("用户名")
        password = st.text_input("密码", type="password")
        submitted = st.form_submit_button("登录", type="primary", width="stretch")
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
    _persist_login()
    st.success(f"欢迎回来，{me['username']}！")
    time.sleep(0.5)
    st.rerun()


def page_register():
    st.markdown(_HERO, unsafe_allow_html=True)
    st.title("📝 注册")
    with st.form("register-form"):
        username = st.text_input("用户名（3–40 字符）")
        password = st.text_input("密码（至少 6 位）", type="password")
        submitted = st.form_submit_button("注册", type="primary", width="stretch")
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
    _persist_login()
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
    st.markdown(f"### {html.escape(u['username'])}　{_role_badge(u.get('role'))}", unsafe_allow_html=True)
    st.caption(f"注册时间：{u.get('join_time')}")
    submit_count = int(u.get("submit_count") or 0)
    resolve_count = int(u.get("resolve_count") or 0)
    rate = f"{resolve_count / submit_count * 100:.1f}%" if submit_count else "—"
    c1, c2, c3 = st.columns(3)
    c1.metric("提交数", submit_count)
    c2.metric("通过题数", resolve_count)
    c3.metric("通过率", rate)

    st.divider()
    st.subheader("修改用户名")
    with st.form("rename-user-form"):
        username = st.text_input("新用户名（3–40 字符）", value=u["username"])
        submitted = st.form_submit_button("保存用户名", type="primary")
    if submitted:
        username = username.strip()
        if not 3 <= len(username) <= 40:
            st.error("用户名长度需在 3–40 字符之间。")
        elif username == u["username"]:
            st.info("用户名未发生变化。")
        else:
            try:
                data = api("PUT", f"/api/users/{me['user_id']}/username",
                           json={"username": username})
                st.session_state["me"]["username"] = data["username"]
                st.success("用户名已更新。")
                time.sleep(0.4)
                st.rerun()
            except ApiError as e:
                friendly_error(e)


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
        rows = "".join(
            f"<tr><td class='oj-mono'>#{u.get('user_id')}</td>"
            f"<td>{html.escape(str(u.get('username', '')))}</td>"
            f"<td>{_role_badge(u.get('role'))}</td>"
            f"<td>{html.escape(str(u.get('join_time', '—')))}</td>"
            f"<td>{u.get('submit_count', 0)}</td>"
            f"<td>{u.get('resolve_count', 0)}</td></tr>"
            for u in users)
        st.markdown(_html_table(["ID", "用户名", "角色", "注册时间", "提交数", "通过题数"], rows),
                    unsafe_allow_html=True)

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


def page_languages():
    """登录用户查询并维护评测语言注册表。"""
    st.title("🧩 语言管理")
    st.caption("评测器根据这里的配置自动选择源码扩展名、编译命令和运行命令。")

    try:
        names = (api("GET", "/api/languages/") or {}).get("name") or []
    except ApiError as e:
        friendly_error(e)
        return

    st.subheader("当前支持的语言")
    if names:
        rows = "".join(
            f"<tr><td>{index}</td><td class='oj-mono'>{html.escape(str(name))}</td></tr>"
            for index, name in enumerate(names, 1)
        )
        st.markdown(_html_table(["序号", "语言名称"], rows), unsafe_allow_html=True)
    else:
        st.info("当前还没有已注册的语言。")

    st.divider()
    st.subheader("注册新语言")
    st.caption("命令不会经 shell 执行；请使用 {src} 表示源码路径，使用 {exe} 表示可执行文件路径。")
    with st.form("language-form"):
        c1, c2 = st.columns(2)
        name = c1.text_input("语言名称", placeholder="例如 go")
        file_ext = c2.text_input("源码扩展名", placeholder="例如 .go")
        compile_cmd = st.text_input("编译命令（解释型语言可留空）", placeholder="例如 go build -o {exe} {src}")
        run_cmd = st.text_input("运行命令", placeholder="例如 {exe} 或 python3 {src}")
        c3, c4 = st.columns(2)
        time_limit = c3.number_input("默认时间限制（秒，0 表示使用系统默认）", min_value=0.0,
                                     value=0.0, step=0.5)
        memory_limit = c4.number_input("默认内存限制（MB，0 表示使用系统默认）", min_value=0,
                                       value=0, step=1)
        submitted = st.form_submit_button("注册语言", type="primary", width="stretch")

    if not submitted:
        return
    if not name.strip() or not file_ext.strip() or not run_cmd.strip():
        st.error("语言名称、源码扩展名和运行命令不能为空。")
        return

    body = {
        "name": name.strip(),
        "file_ext": file_ext.strip(),
        "compile_cmd": compile_cmd.strip() or None,
        "run_cmd": run_cmd.strip(),
    }
    if time_limit > 0:
        body["time_limit"] = float(time_limit)
    if memory_limit > 0:
        body["memory_limit"] = int(memory_limit)
    try:
        api("POST", "/api/languages/", json=body)
    except ApiError as e:
        friendly_error(e)
        return
    st.success(f"语言 {name.strip()} 注册成功。")
    time.sleep(0.4)
    st.rerun()


@st.dialog("访问日志详情")
def _show_access_log_detail(row: dict):
    """在当前页展示审计记录，不创建新页面或改变浏览器历史。"""
    action_text = {"view_logs": "查看评测日志"}.get(row.get("action"), row.get("action") or "—")
    status = str(row.get("status") or "")
    fields = (
        ("日志 ID", row.get("log_id")),
        ("用户名", row.get("username") or "—"),
        ("题目 ID", row.get("problem_id") or "—"),
        ("行为", action_text),
        ("访问结果", ACCESS_TEXT.get(status, status or "—")),
        ("发生时间", row.get("time") or "—"),
    )
    for label, value in fields:
        label_col, value_col = st.columns([1, 2])
        label_col.markdown(f"**{label}**")
        value_col.text(str(value))


def page_audit_logs():
    """Step 5 日志与权限：访问审计列表（仅管理员，GET /api/logs/access/）。

    接口返回纯数组无 total：当前页满时额外探测下一页；
    筛选/页码存 session_state。
    """
    st.title("🛡 访问审计")
    st.caption("记录所有评测日志查看行为（允许与拒绝）· 仅管理员可见")
    if flash := st.session_state.pop("audit_flash", None):
        st.success(flash)

    if "audit_username" not in st.session_state:
        st.session_state["audit_username"] = ""
    if "audit_problem" not in st.session_state:
        st.session_state["audit_problem"] = ""
    if "audit_page" not in st.session_state:
        st.session_state["audit_page"] = 1

    c1, c2, c3 = st.columns(3)
    username = c1.text_input("用户名（筛选）", value=st.session_state["audit_username"],
                             placeholder="留空为全部")
    problem_id = c2.text_input("题目 ID（筛选）", value=st.session_state["audit_problem"], placeholder="留空为全部")
    if c3.button("应用筛选"):
        st.session_state["audit_username"] = username.strip()
        st.session_state["audit_problem"] = problem_id.strip()
        st.session_state["audit_page"] = 1
        st.session_state.pop("audit_confirm_delete", None)
        st.rerun()

    page_size = 20
    params = {"page": st.session_state["audit_page"], "page_size": page_size}
    if st.session_state["audit_username"]:
        params["username"] = st.session_state["audit_username"]
    if st.session_state["audit_problem"]:
        params["problem_id"] = st.session_state["audit_problem"]
    try:
        rows = api("GET", "/api/logs/access/", params=params)
        # 后端使用 page_size 计算 offset，不能用“多取 1 条”，否则
        # 下一页会永久跳过一条记录。当前页满时用同一页长探测下页。
        has_next = False
        if len(rows) == page_size:
            probe_params = {**params, "page": st.session_state["audit_page"] + 1}
            has_next = bool(api("GET", "/api/logs/access/", params=probe_params))
    except ApiError as e:
        friendly_error(e)
        return

    if not rows:
        st.info("暂无审计记录。")
    else:
        header = st.columns([1.35, 0.8, 1.05, 0.9, 1.65, 1.5])
        for col, label in zip(header, ("用户名", "题目", "行为", "结果", "时间", "操作")):
            col.markdown(f"**{label}**")
        for row in rows:
            log_id = str(row["log_id"])
            with st.container(border=True):
                user_col, problem_col, action_col, status_col, time_col, ops_col = st.columns(
                    [1.35, 0.8, 1.05, 0.9, 1.65, 1.5]
                )
                user_col.text(str(row.get("username") or "—"))
                problem_col.text(str(row.get("problem_id") or "—"))
                action_col.text({"view_logs": "查看日志"}.get(
                    row.get("action"), str(row.get("action") or "—")
                ))
                status_col.markdown(_access_badge(row.get("status")), unsafe_allow_html=True)
                time_col.text(str(row.get("time") or "—"))
                detail_col, delete_col = ops_col.columns(2)
                if detail_col.button("查看详情", key=f"audit-detail-{log_id}",
                                     width="stretch"):
                    _show_access_log_detail(row)
                if delete_col.button("删除", key=f"audit-delete-{log_id}",
                                     width="stretch"):
                    st.session_state["audit_confirm_delete"] = log_id
                if st.session_state.get("audit_confirm_delete") == log_id:
                    st.warning(
                        f"确认删除 #{log_id}（{row.get('username') or '—'} / "
                        f"题目 {row.get('problem_id') or '—'}）？删除后不可恢复。"
                    )
                    confirm_col, cancel_col = st.columns(2)
                    if confirm_col.button(
                        "确认删除", type="primary", width="stretch",
                        key=f"audit-confirm-delete-{log_id}",
                    ):
                        try:
                            api("DELETE", f"/api/logs/access/{log_id}")
                        except ApiError as exc:
                            friendly_error(exc)
                        else:
                            st.session_state.pop("audit_confirm_delete", None)
                            if len(rows) == 1 and st.session_state["audit_page"] > 1:
                                st.session_state["audit_page"] -= 1
                            st.session_state["audit_flash"] = f"访问日志 #{log_id} 已删除。"
                            st.rerun()
                    if cancel_col.button(
                        "取消", width="stretch", key=f"audit-cancel-delete-{log_id}",
                    ):
                        st.session_state.pop("audit_confirm_delete", None)
                        st.rerun()

    prev_col, info_col, next_col = st.columns([1, 2, 1])
    if prev_col.button("上一页", disabled=st.session_state["audit_page"] <= 1, width="stretch"):
        st.session_state["audit_page"] -= 1
        st.rerun()
    info_col.markdown(f"第 {st.session_state['audit_page']} 页")
    if next_col.button("下一页", disabled=not has_next, width="stretch"):
        st.session_state["audit_page"] += 1
        st.rerun()


# ---------- 任务 2：题目页面组 ----------

def page_problems():
    st.session_state["prob_view"] = "list"
    st.session_state.pop("prob_id", None)
    _problem_list()


def page_problem_new():
    st.session_state["prob_view"] = "new"
    st.session_state.pop("prob_id", None)
    _problem_form()


def page_problem_detail():
    problem_id = st.query_params.get("problem")
    if not problem_id or not _valid_problem_id(problem_id):
        st.error("题目 ID 无效。")
        if st.button("← 返回题目列表"):
            _go("problems")
        return
    st.session_state["prob_id"] = str(problem_id)
    st.session_state["prob_view"] = "detail"
    _problem_detail()


def page_problem_edit():
    problem_id = st.query_params.get("problem")
    if not problem_id or not _valid_problem_id(problem_id):
        st.error("题目 ID 无效。")
        if st.button("← 返回题目列表"):
            _go("problems")
        return
    st.session_state["prob_id"] = str(problem_id)
    st.session_state["prob_view"] = "edit"
    _problem_form()


def _problem_list():
    st.title("📋 题目列表")
    try:
        problems = api("GET", "/api/problems/")
    except ApiError as e:
        friendly_error(e)
        return
    st.caption(f"共 {len(problems)} 题")
    if st.button("➕ 新建题目", type="primary"):
        _go("problem_new")
    if not problems:
        st.info("暂无题目，点击上方按钮创建第一道题。")
        return
    # 题名本身就是详情入口。使用 Streamlit 原生按钮在当前会话内切换，
    # 避免普通 HTML 链接整页重载时丢失刚建立的登录状态。
    head_id, head_title = st.columns([1, 6])
    head_id.markdown("**题目 ID**")
    head_title.markdown("**标题（点击进入详情）**")
    for problem in problems:
        id_col, title_col = st.columns([1, 6])
        id_col.markdown(f"<span class='oj-mono'>{html.escape(str(problem['id']))}</span>",
                        unsafe_allow_html=True)
        if title_col.button(
            problem["title"], key=f"problem-title-{problem['id']}", type="tertiary",
        ):
            _go("problem_detail", problem=problem["id"])


def _problem_detail():
    pid = st.session_state.get("prob_id")
    me = st.session_state.get("me")
    if st.button("← 返回列表"):
        _go("problems")
    try:
        p = api("GET", f"/api/problems/{pid}")
    except ApiError as e:
        friendly_error(e)
        return

    st.title(p["title"])
    meta = (f"题目 ID：<span class='oj-mono'>{html.escape(p['id'])}</span>"
            f" · 时间限制 {p.get('time_limit')}s · 内存限制 {p.get('memory_limit')}MB")
    if p.get("difficulty"):
        meta += f" · {_diff_badge(p['difficulty'])}"
    if p.get("author"):
        meta += f" · 作者 {html.escape(str(p['author']))}"
    if p.get("source"):
        meta += f" · 来源 {html.escape(str(p['source']))}"
    st.markdown(f"<p style='color:#6b7280;font-size:.95rem'>{meta}</p>", unsafe_allow_html=True)
    if p.get("tags"):
        st.markdown(" ".join(_badge(t, "blue") for t in p["tags"]), unsafe_allow_html=True)

    # 力扣式双栏布局：左侧题目信息，右侧内嵌代码提交面板（做题不用跳页）
    left, right = st.columns([3, 2], gap="large")
    with left:
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
    with right:
        _submit_panel(pid)

    c1, c2, c3 = st.columns(3)
    if c1.button("✏️ 编辑题目", width="stretch"):
        _go("problem_edit", problem=pid)
    if c2.button("📜 本题提交记录", width="stretch"):
        _go("submissions", problem=pid)
    if me.get("role") == "admin":
        if c3.button("🗑 删除题目", width="stretch"):
            st.session_state["confirm_delete"] = pid
        if st.session_state.get("confirm_delete") == pid:
            st.error(f"确认删除题目 {pid}？此操作不可恢复。")
            a, b = st.columns(2)
            if a.button("✅ 确认删除", width="stretch"):
                try:
                    api("DELETE", f"/api/problems/{pid}")
                    st.session_state.pop("confirm_delete", None)
                    st.success("已删除。")
                    time.sleep(0.5)
                    _go("problems")
                except ApiError as e:
                    friendly_error(e)
            if b.button("取消", width="stretch"):
                st.session_state.pop("confirm_delete", None)
                st.rerun()

        # Step 5：配置日志可见性（PUT /api/problems/{id}/log_visibility）
        st.divider()
        st.subheader("⚙️ 日志详情查看权限")
        st.caption("只有管理员可以修改。设置为公开后，所有已登录用户都能查看本题的测试点日志详情。")
        public = st.radio(
            "谁可以查看日志详情",
            [False, True],
            index=1 if p.get("public_cases", False) else 0,
            format_func=lambda value: "所有已登录用户" if value else "仅提交者和管理员",
            horizontal=True,
            key=f"log-visibility-{pid}",
        )
        if st.button("保存日志权限设置", type="primary"):
            if public == bool(p.get("public_cases", False)):
                st.info("未发生变化。")
            else:
                try:
                    api("PUT", f"/api/problems/{pid}/log_visibility", json={"public_cases": public})
                    st.success("已更新日志详情查看权限。")
                    time.sleep(0.4)
                    st.rerun()
                except ApiError as e:
                    friendly_error(e)


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
        if is_edit:
            _go("problem_detail", problem=pid)
        else:
            _go("problems")

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
    if not _valid_problem_id(pid_in.strip()):
        errors.append("题目 ID 必须是数字。")
    if not title.strip():
        errors.append("标题不能为空。")
    for name, value in (("题目描述", description), ("输入格式", input_description),
                        ("输出格式", output_description), ("数据范围", constraints)):
        if not value.strip():
            errors.append(f"{name}不能为空。")
    sample_list = case_list = None
    try:
        sample_list = json.loads(samples)
        if not isinstance(sample_list, list) or not sample_list or any(
                not isinstance(s, dict) or not isinstance(s.get("input"), str)
                or not isinstance(s.get("output"), str) for s in sample_list):
            raise ValueError
    except (ValueError, json.JSONDecodeError):
        errors.append("样例必须是 JSON 数组，每项含字符串 input/output。")
    try:
        case_list = json.loads(testcases)
        if not isinstance(case_list, list) or not case_list or any(
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
    time.sleep(0.5)
    _go("problem_detail", problem=pid_in.strip())


# ---------- 任务 3：评测与提交页面组 ----------

def _submit_panel(pid: str):
    """题目详情右侧的内嵌提交面板（力扣式布局）：选语言 → 写代码 → 提交 → 结果就地轮询展示。

    代码/语言按题目 id 单独缓存（widget key），切换题目互不干扰。
    """
    st.markdown("#### 🚀 提交代码")
    try:
        langs = (api("GET", "/api/languages/") or {}).get("name") or []
    except ApiError as e:
        friendly_error(e)
        langs = []
    if not langs:
        st.info("暂无可用语言，请联系管理员注册。")
        return
    lang = st.selectbox("语言", langs, key=f"lang_{pid}")
    code = st.text_area("代码", height=340, placeholder="在此粘贴你的代码", key=f"code_{pid}")
    if not st.button("提交评测", type="primary", width="stretch", key=f"submit_{pid}"):
        return
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
    st.session_state["sub_filter_problem"] = st.query_params.get("problem", "")
    st.session_state["sub_view"] = "list"
    st.session_state.pop("sub_id", None)
    _submission_list()


def page_submission_detail():
    submission_id = st.query_params.get("submission")
    if not submission_id or not str(submission_id).isdigit():
        st.error("提交 ID 无效。")
        if st.button("← 返回评测记录"):
            _go("submissions")
        return
    st.session_state["sub_id"] = str(submission_id)
    st.session_state["sub_view"] = "detail"
    _submission_detail()


def _submission_list():
    me = st.session_state.get("me")
    st.title("📜 评测记录")
    preset_problem = st.session_state.get("sub_filter_problem", "")
    c1, c2 = st.columns(2)
    status = c1.selectbox("状态", ["全部", "pending", "success", "error"],
                          format_func=lambda x: {"全部": "全部", "pending": "等待中",
                                                 "success": "评测完成", "error": "评测失败"}[x])
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
    # api.md：user_id/problem_id 一级条件不可全空
    if not params.get("problem_id") and not params.get("user_id"):
        st.info("管理员查询评测记录需指定筛选条件：请填写题目 ID 或用户 ID。")
        return
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
    # 力扣评测列表风格：状态徽章 + 等宽 ID（pending/error 条目仅返回 id/status，其余列占位）
    is_admin = me.get("role") == "admin"
    headers = ["ID", "题目"] + (["用户"] if is_admin else []) + ["语言", "状态", "得分", "时间"]
    rows = "".join(
        f"<tr><td class='oj-mono'>#{s.get('submission_id')}</td>"
        f"<td class='oj-mono'>{html.escape(str(s.get('problem_id', '—')))}</td>"
        + (f"<td>{html.escape(str(s.get('user_id', '—')))}</td>" if is_admin else "")
        + f"<td>{html.escape(str(s.get('language', '—')))}</td>"
          f"<td>{_status_badge(s.get('status'))}</td>"
          f"<td>{s.get('score', '—')}</td>"
          f"<td>{html.escape(str(s.get('submit_time', '—')))}</td></tr>"
        for s in subs)
    st.markdown(_html_table(headers, rows), unsafe_allow_html=True)
    sel = st.selectbox("查看提交详情", [s["submission_id"] for s in subs],
                       format_func=lambda x: f"#{x}")
    if st.button("打开详情"):
        _go("submission_detail", submission=sel, problem=problem.strip())


@st.fragment(run_every=1.5)
def _submission_detail():
    sid = st.session_state.get("sub_id")
    me = st.session_state.get("me")
    if st.button("← 返回列表"):
        _go("submissions", problem=st.query_params.get("problem", ""))
    try:
        s = api("GET", f"/api/submissions/{sid}")
    except ApiError as e:
        friendly_error(e)
        return
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


@st.fragment(run_every=1.5)
def _poll_submission(sid: str, show_log: bool):
    """片段轮询保留登录、导航及表单状态，评测期间页面仍可操作。"""
    try:
        result = api("GET", f"/api/submissions/{sid}")
    except ApiError as exc:
        friendly_error(exc)
        return
    _render_submission(result, show_log=show_log)


def _info_text(v):
    """兼容 compile_info/run_info 的对象结构与旧版裸字符串。"""
    return v.get("message") if isinstance(v, dict) else v


def _render_submission(s: dict, show_log: bool):
    status = s.get("status", "pending")
    st.markdown(f"### 提交 #{s.get('submission_id')}　{_status_badge(status)}", unsafe_allow_html=True)
    meta = " · ".join(x for x in (
        f"题目 {s.get('problem_id', '—')}", f"用户 {s.get('user_id', '—')}",
        f"语言 {s.get('language', '—')}", f"提交于 {s.get('submit_time', '—')}") if x)
    st.caption(meta)
    if status == "pending":
        st.info("⏳ 正在评测，请稍候…")
        return
    score = s.get("score")
    counts = s.get("counts")            # api.md：本题总分数
    verdicts = s.get("verdicts") or {}  # extra：各结果统计
    c1, c2 = st.columns(2)
    c1.metric("得分", score if score is not None else "—")
    c2.metric("总分", counts if counts is not None else "—")
    if status == "success":
        if counts and score == counts:
            st.success("全部测试点通过。")
        else:
            st.warning("评测已完成，部分或全部测试点未通过；请查看结果及运行信息。")
    if verdicts:
        st.markdown(" ".join(_verdict_pill(k, v) for k, v in verdicts.items()),
                    unsafe_allow_html=True)
    # CE / RE / TLE 等错误明确展示（任务 3）
    # compile_info / run_info 为 api.md 对象结构 {"result", "message"}，展示 message
    if s.get("compile_info"):
        info = s["compile_info"]
        compiled = isinstance(info, dict) and info.get("result") == "success"
        st.markdown("#### 编译信息（编译成功）" if compiled else "#### 编译信息（编译失败）")
        st.code(_info_text(info) or "编译成功")
    if s.get("run_info"):
        st.markdown("#### 运行信息")
        st.code(_info_text(s["run_info"]))
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


# ---------- Advance：AI 智能命题 ----------

def _ai_usage_panel(usage: dict):
    """R4：Token 用量与费用展示，附计价依据说明（advance.md 要求透明）。"""
    if not usage:
        st.caption("模型尚未返回用量，当前 Token 用量和费用未知。")
        return
    c1, c2, c3 = st.columns(3)
    c1.metric("输入 Token", usage.get("input_tokens", "—"))
    c2.metric("输出 Token", usage.get("output_tokens", "—"))
    c3.metric("总 Token", usage.get("total_tokens", "—"))
    cost = usage.get("cost")
    c4, c5, c6 = st.columns(3)
    c4.metric("费用", f"{cost} {usage.get('currency', '')}" if cost is not None else "—")
    c5.metric("计价依据", {"provider": "接口返回", "config": "手动配置", "unknown": "未配置", "mixed": "多次调用"}.get(usage.get("price_source"), "—"))
    c6.metric("用量来源", "字符估算" if usage.get("estimated") else "接口返回")
    notes = {
        "provider": "费用由模型接口直接返回。",
        "config": f"费用 = 输入Token/{usage.get('price_unit')} × {usage.get('input_price')}"
                  f" + 输出Token/{usage.get('price_unit')} × {usage.get('output_price')}（{usage.get('currency')}）。",
        "unknown": "未填写输入/输出价格，无法自动计算费用；可在模型配置中填写价格（不同模型、不同时段价格可能不同）。",
    }
    st.caption(f"计价依据：{notes.get(usage.get('price_source'), '—')}"
               f"{'；模型接口未完整返回 Token 用量，缺失部分按字符数/4 估算，包含系统提示词；估算不等同账单。' if usage.get('estimated') else ''}")


def _render_ai_result(result: dict, problem_id: str | None):
    """R1：生成结果预览 + 经已有题目接口导入题库（与基础功能衔接）。"""
    st.divider()
    st.subheader(f"生成的题目：{result.get('title', '')}（{result.get('id', '')}）")
    meta = f"难度 {result.get('difficulty') or '—'} · 时间限制 {result.get('time_limit')}s" \
           f" · 内存限制 {result.get('memory_limit')}MB"
    if result.get("tags"):
        meta += " · " + " ".join(f"`{t}`" for t in result["tags"])
    st.caption(meta)
    tab_desc, tab_samples, tab_cases = st.tabs(["题目描述", "样例", "测试点"])
    with tab_desc:
        st.markdown(result.get("description") or "—")
        st.markdown("#### 输入格式")
        st.markdown(result.get("input_description") or "—")
        st.markdown("#### 输出格式")
        st.markdown(result.get("output_description") or "—")
        if result.get("constraints"):
            st.markdown("#### 数据范围")
            st.markdown(result["constraints"])
    with tab_samples:
        for i, s in enumerate(result.get("samples") or []):
            c1, c2 = st.columns(2)
            c1.markdown(f"**样例 {i + 1} · 输入**")
            c1.code(s.get("input", ""))
            c2.markdown(f"**样例 {i + 1} · 输出**")
            c2.code(s.get("output", ""))
    with tab_cases:
        st.caption(f"共 {len(result.get('testcases') or [])} 个测试点")
        for t in result.get("testcases") or []:
            with st.expander(f"测试点 #{t.get('id', '?')}"):
                st.code(t.get("input", ""), language="text")
                st.code(t.get("output", ""), language="text")

    reviewed = st.text_area("审阅并修改题目 JSON（保存时使用此内容）",
                            value=json.dumps(result, ensure_ascii=False, indent=2),
                            height=260, key=f"ai-review-{st.session_state.get('ai_task_id')}")
    label = f"💾 保存修改到 {problem_id}" if problem_id else "💾 保存为新题目"
    if st.button(label, type="primary", width="stretch"):
        try:
            result = json.loads(reviewed)
            if not isinstance(result, dict):
                raise ValueError("题目必须是 JSON 对象")
        except ValueError:
            st.error("题目 JSON 格式错误，请检查后再保存。")
            return
        try:
            if problem_id:
                api("PUT", f"/api/problems/{problem_id}", json=result)
                st.success(f"已保存修改到题目 {problem_id}。")
            else:
                new_id = api("POST", "/api/problems/", json=result)["id"]
                st.success(f"已保存为新题目 {new_id}。")
            time.sleep(0.4)
            _go("problem_detail", problem=problem_id or result.get("id"))
        except ApiError as e:
            friendly_error(e)


def _render_ai_conversation(task_id: int):
    """展示当前版本的完整对话链，并允许回看任意旧版本。"""
    try:
        turns = api("GET", f"/api/ai/problem-tasks/{task_id}/conversation") or []
    except ApiError:
        return
    if len(turns) <= 1:
        return

    with st.expander(f"💬 修改记录（{len(turns)} 轮）", expanded=False):
        for index, turn in enumerate(turns, start=1):
            turn_id = turn.get("task_id")
            kind = "初始需求" if turn.get("parent_task_id") is None else "修改要求"
            title = f" · {turn['result_title']}" if turn.get("result_title") else ""
            st.markdown(f"**第 {index} 轮 · 任务 #{turn_id}{title}**")
            st.write(f"{kind}：{turn.get('requirement') or '—'}")
            st.caption(" · ".join(x for x in (
                AI_MODE_LABELS.get(turn.get("generation_mode"), "自定义模型"),
                f"状态 {turn.get('status') or '—'}",
                turn.get("created_at") or "",
            ) if x))
            if turn_id != task_id and st.button(
                "查看这一版", key=f"ai-conversation-{task_id}-{turn_id}",
            ):
                _go("ai_task", task=turn_id)


def _render_ai_refine(task: dict):
    """完成结果后的连续修改入口；每轮都创建不可变的新版本。"""
    st.divider()
    st.subheader("💬 继续修改这道题")
    st.caption("描述不满意的地方即可生成下一版；当前版本不会被覆盖，可在修改记录中随时回看。")

    cfg = {}
    try:
        cfg = api("GET", "/api/ai/model-config") or {}
    except ApiError:
        pass
    supports_modes = bool(cfg.get("api_key_configured") and
                          _supports_deepseek_modes(cfg.get("provider_url")))
    task_id = int(task["task_id"])
    with st.form(f"ai-refine-form-{task_id}"):
        requirement = st.text_area(
            "本轮修改要求",
            placeholder="例如：题面再简洁一些，增加一组边界样例，并把难度调整为中等",
            height=110,
            key=f"ai-refine-requirement-{task_id}",
        )
        generation_mode = _ai_mode_radio(
            current=task.get("generation_mode"),
            disabled=not supports_modes,
            key=f"ai-refine-mode-{task_id}",
        )
        if not supports_modes:
            st.caption("当前提供商将继续使用模型配置中的自定义模型。")
        submitted = st.form_submit_button("✨ 生成下一版", type="primary", width="stretch")
    if not submitted:
        return
    if not requirement.strip():
        st.error("请输入本轮修改要求。")
        return
    try:
        next_task = api("POST", f"/api/ai/problem-tasks/{task_id}/refine", json={
            "requirement": requirement.strip(),
            "generation_mode": generation_mode if supports_modes else None,
        })
    except ApiError as exc:
        friendly_error(exc)
        return
    st.session_state["ai_refine_notice"] = (
        f"已基于任务 #{task_id} 创建下一版任务 #{next_task['task_id']}。"
    )
    _go("ai_task", task=next_task["task_id"])


@st.fragment(run_every=1.5)
def _ai_task_detail():
    tid = st.session_state.get("ai_task_id")
    if st.button("← 返回 AI 命题页"):
        _go("ai")
    try:
        d = api("GET", f"/api/ai/problem-tasks/{tid}")
    except ApiError as e:
        friendly_error(e)
        return
    status = d.get("status", "pending")
    st.title(f"AI 命题任务 #{tid}")
    if notice := st.session_state.pop("ai_retry_notice", None):
        st.success(notice)
    if notice := st.session_state.pop("ai_refine_notice", None):
        st.success(notice)
    st.caption(" · ".join(x for x in (
        d.get("requirement", ""), f"改编自 {d['problem_id']}" if d.get("problem_id") else "",
        AI_MODE_LABELS.get(d.get("generation_mode"), ""),
        f"模型 {d.get('model') or '—'}", f"创建于 {d.get('created_at') or '—'}") if x))
    st.markdown(f"### {_ai_status_badge(status)}", unsafe_allow_html=True)
    st.progress(min(float(d.get("progress") or 0), 1.0))
    _render_ai_conversation(int(tid))

    if status in ("pending", "running"):
        # 每秒自动刷新实现实时进度（R3）；页面不阻塞，「中断任务」可随时点击
        st.caption("进度每 1.5 秒自动更新；中断任务会终止后台执行。")
        _ai_usage_panel(d.get("usage"))
        if st.button("🛑 中断任务", width="stretch"):
            try:
                api("PUT", f"/api/ai/problem-tasks/{tid}/cancel")
                st.success("任务已中断，后台执行已终止。")
                time.sleep(0.3)
                st.rerun()
            except ApiError as e:
                friendly_error(e)
        return

    if status == "done" and d.get("result"):
        _render_ai_result(d["result"], d.get("problem_id"))
        _render_ai_refine(d)
    elif status == "failed":
        st.error((d.get("result") or {}).get("error") or "未知错误")
        st.caption("重新开始会使用当前最新模型配置创建新任务，原失败记录与已产生的用量会保留。")
        if st.button("🔄 重新开始此任务", type="primary", width="stretch"):
            try:
                restarted = api("POST", f"/api/ai/problem-tasks/{tid}/retry")
            except ApiError as exc:
                friendly_error(exc)
            else:
                st.session_state["ai_retry_notice"] = (
                    f"已从任务 #{tid} 创建新任务 #{restarted['task_id']}。"
                )
                _go("ai_task", task=restarted["task_id"])
    elif status == "cancelled":
        st.info("任务已中断，后台执行已终止，可返回 AI 命题页重新创建任务。")
    _ai_usage_panel(d.get("usage"))


def page_ai():
    st.session_state["ai_view"] = "home"
    st.session_state.pop("ai_task_id", None)
    _ai_home()


def page_ai_task():
    task_id = st.query_params.get("task")
    if not task_id or not str(task_id).isdigit():
        st.error("AI 任务 ID 无效。")
        if st.button("← 返回 AI 命题页"):
            _go("ai")
        return
    st.session_state["ai_task_id"] = int(task_id)
    st.session_state["ai_view"] = "task"
    _ai_task_detail()


def _ai_home():
    st.title("✨ AI 智能命题")
    st.caption("配置大模型后，输入命题需求即可自动生成符合题库规范的题目，实时查看进度并可导入题库。")
    st.caption(
        f"推理模型可能需要数分钟；单次请求最长等待 "
        f"{config.AI_REQUEST_TIMEOUT_SECONDS:g} 秒（可通过 OJ_AI_REQUEST_TIMEOUT 调整）。"
    )

    # R2：模型配置（密钥加密存储，保存后不回显）
    cfg = {}
    try:
        cfg = api("GET", "/api/ai/model-config") or {}
    except ApiError:
        pass   # 未配置也可打开页面
    with st.expander("⚙️ 模型配置", expanded=not cfg.get("api_key_configured")):
        if cfg.get("api_key_configured"):
            st.caption(f"当前模型：{cfg.get('model')}（{cfg.get('provider_url')}）· 密钥已配置（出于安全不回显）")
        else:
            st.caption("尚未配置模型。密钥加密存储，任何接口都不会回显。")
        with st.form("ai-config-form"):
            provider_url = st.text_input("提供商 URL（OpenAI 兼容 chat/completions 完整接口地址）",
                                         value=cfg.get("provider_url", ""))
            model = st.text_input("模型名称", value=cfg.get("model", ""))
            api_key = st.text_input("模型密钥", type="password",
                                    help="仅加密存储；保存后不回显，修改配置时需重新填写")
            c1, c2 = st.columns(2)
            input_price = c1.text_input("输入价格（所选币种/计价单位，可选）",
                                        value=str(cfg["input_price"]) if cfg.get("input_price") is not None else "")
            output_price = c2.text_input("输出价格（所选币种/计价单位，可选）",
                                         value=str(cfg["output_price"]) if cfg.get("output_price") is not None else "")
            currency = st.selectbox("价格币种", ["CNY", "USD"], index=1 if cfg.get("currency") == "USD" else 0)
            price_unit = st.text_input("计价单位（Token 数）", value=str(cfg.get("price_unit") or 1000000))
            st.caption("⚠ 不同模型、不同时段的计费价格可能不同（部分厂商设有错峰优惠时段），请按实际调用时段的官方价格填写。")
            if st.form_submit_button("保存配置", width="stretch"):
                errors = []
                if not provider_url.strip():
                    errors.append("提供商 URL 不能为空。")
                if not model.strip():
                    errors.append("模型名称不能为空。")
                if not api_key:
                    errors.append("模型密钥不能为空。")
                prices = {}
                for name, text in (("input_price", input_price), ("output_price", output_price)):
                    if text.strip():
                        try:
                            v = float(text.strip())
                            if v < 0:
                                raise ValueError
                            prices[name] = v
                        except ValueError:
                            errors.append(f"{name} 必须是非负数字。")
                    else:
                        prices[name] = None
                try:
                    unit = int(price_unit.strip())
                    if unit < 1:
                        raise ValueError
                except ValueError:
                    errors.append("计价单位必须是正整数。")
                if errors:
                    for e in errors:
                        st.error(e)
                else:
                    try:
                        api("PUT", "/api/ai/model-config", json={
                            "provider_url": provider_url.strip(), "model": model.strip(),
                            "api_key": api_key, "price_unit": unit, "currency": currency, **prices,
                        })
                        st.success("模型配置已保存。")
                        time.sleep(0.4)
                        st.rerun()
                    except ApiError as e:
                        friendly_error(e)

    # 新建命题任务
    st.subheader("新建命题任务")
    problems = []
    try:
        problems = api("GET", "/api/problems/")
    except ApiError:
        pass
    with st.form("ai-task-form"):
        requirement = st.text_area("命题需求", height=120,
                                   placeholder="例如：出一道考查二分查找的题目，难度中等，n ≤ 10^6，包含边界测试点")
        pid = st.selectbox("参考/改编题目（可选）", [""] + [p["id"] for p in problems],
                           format_func=lambda x: x or "— 新题目 —")
        supports_modes = bool(cfg.get("api_key_configured") and
                              _supports_deepseek_modes(cfg.get("provider_url")))
        generation_mode = _ai_mode_radio(
            current="balanced",
            disabled=not supports_modes,
            key="ai-new-task-mode",
        )
        if not supports_modes:
            st.caption("三档模式需要先配置 DeepSeek 官方 API；其他提供商继续使用已配置的自定义模型。")
        if st.form_submit_button("✨ 创建命题任务", width="stretch"):
            if not requirement.strip():
                st.error("命题需求不能为空。")
            else:
                try:
                    resp = api("POST", "/api/ai/problem-tasks/", json={
                        "requirement": requirement.strip(),
                        "problem_id": pid or None,
                        "generation_mode": generation_mode if supports_modes else None,
                    })
                    st.success("任务已创建，开始生成…")
                    time.sleep(0.4)
                    _go("ai_task", task=resp["task_id"])
                except ApiError as e:
                    friendly_error(e)

    # 任务记录
    st.subheader("任务记录")
    tasks = []
    try:
        tasks = api("GET", "/api/ai/problem-tasks/") or []
    except ApiError:
        pass
    if not tasks:
        st.info("暂无任务，创建第一个命题任务吧。")
        return
    rows_html = "".join(
        f"<tr><td class='oj-mono'>#{t.get('task_id')}</td>"
        f"<td>{_ai_status_badge(t.get('status'))}</td>"
        f"<td>{round((t.get('progress') or 0) * 100)}%</td>"
        f"<td>{html.escape(AI_MODE_LABELS.get(t.get('generation_mode'), '自定义'))}</td>"
        f"<td>{html.escape(str(t.get('model', '—')))}</td>"
        f"<td>{html.escape(str(t.get('created_at', '—')))}</td></tr>"
        for t in tasks)
    st.markdown(_html_table(["ID", "状态", "进度", "模式", "模型", "创建时间"], rows_html),
                unsafe_allow_html=True)
    sel = st.selectbox("查看任务详情", [t["task_id"] for t in tasks], format_func=lambda x: f"#{x}")
    if st.button("打开详情"):
        _go("ai_task", task=sel)


# ---------- 入口 ----------

def _build_pages(me: dict | None) -> dict[str, object]:
    """按登录态创建可访问的 Streamlit 原生页面集。"""
    if not me:
        specs = [
            ("login", page_login, True),
            ("register", page_register, False),
        ]
    else:
        specs = [
            ("problems", page_problems, True),
            ("submissions", page_submissions, False),
            ("languages", page_languages, False),
            ("ai", page_ai, False),
            ("profile", page_profile, False),
            ("problem_new", page_problem_new, False),
            ("problem_detail", page_problem_detail, False),
            ("problem_edit", page_problem_edit, False),
            ("submission_detail", page_submission_detail, False),
            ("ai_task", page_ai_task, False),
        ]
        if me.get("role") == "admin":
            specs += [
                ("users", page_admin_users, False),
                ("audit", page_audit_logs, False),
            ]
    return {
        key: st.Page(func, title=_PAGE_LABELS[key][1], icon=_PAGE_LABELS[key][0],
                     url_path=key, default=is_default,
                     visibility="visible" if key in _SIDEBAR_PAGE_KEYS else "hidden")
        for key, func, is_default in specs
    }


def main():
    global _ACTIVE_PAGES
    _inject_ui()
    restore_state = _restore_login()
    # 登录后的每次完整渲染都同步浏览器 Cookie。登录表单会立即 rerun，
    # 因此不能只依赖提交表单当次短暂挂载的组件来完成持久化。
    token = None
    if st.session_state.get("me"):
        token = st.session_state.get("cookies", {}).get(SESSION_COOKIE)
    clear_cookie = bool(st.session_state.pop("_clear_browser_session_cookie", False))
    cookie_result, cookie_marker = write_session_cookie(
        token, config.SESSION_TTL_SECONDS, clear=clear_cookie,
    )
    if restore_state == "unavailable":
        st.error("暂时无法连接 OJ 后端，登录会话已保留，不需要重新登录。")
        st.caption(st.session_state.get("_session_restore_error", ""))
        if st.button("重试连接", type="primary"):
            st.rerun()
        st.stop()
    if token:
        try:
            browser_token = (
                st.context.cookies.get(BROWSER_SESSION_COOKIE)
                or st.context.cookies.get(SESSION_COOKIE)
            )
        except Exception:
            browser_token = None
        cookie_confirmed = (
            browser_token == token
            or (isinstance(cookie_result, dict)
                and cookie_result.get("present") is True
                and cookie_result.get("marker") == cookie_marker)
        )
        if not cookie_confirmed:
            if (isinstance(cookie_result, dict)
                    and cookie_result.get("marker") == cookie_marker
                    and cookie_result.get("present") is False):
                # Cookie 持久化失败不应阻断当前 Streamlit 会话的正常登录。
                st.warning("当前登录已生效，但浏览器未能保存刷新凭据。")
            else:
                st.info("正在安全保存登录状态…")
                st.stop()
    _ACTIVE_PAGES = _build_pages(st.session_state.get("me"))
    current_page = st.navigation(list(_ACTIVE_PAGES.values()), position="hidden")
    render_sidebar()
    current_page.run()


main()
