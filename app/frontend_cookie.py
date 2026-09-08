"""在浏览器中同步后端会话令牌，供 Streamlit 整页刷新后恢复登录。"""
import hashlib
from pathlib import Path

import streamlit.components.v1 as components

BROWSER_SESSION_COOKIE = "oj_browser_session"

_component = components.declare_component(
    "oj_session_cookie",
    path=str(Path(__file__).with_name("static") / "session_cookie"),
)


def write_session_cookie(
    token: str | None,
    max_age: int,
    *,
    clear: bool = False,
) -> tuple[dict | None, str | None]:
    """同步浏览器 Cookie，并返回前端写入确认。

    空 token 默认为不操作；只有明确登出或后端返回 401 时才传
    ``clear=True``，避免暂时连接失败误删有效会话。
    """
    marker = hashlib.sha256(token.encode()).hexdigest()[:16] if token else None
    result = _component(
        token=token or "", marker=marker or "", max_age=max_age, clear=clear,
        key="oj-session-cookie", default=None,
    )
    return result, marker
