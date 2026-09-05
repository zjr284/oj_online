"""在浏览器中同步后端会话令牌，供 Streamlit 整页刷新后恢复登录。"""
from pathlib import Path

import streamlit.components.v1 as components

_component = components.declare_component(
    "oj_session_cookie",
    path=str(Path(__file__).with_name("static") / "session_cookie"),
)


def write_session_cookie(token: str | None, max_age: int) -> None:
    """写入或删除浏览器 Cookie；组件本身不显示可见内容。"""
    _component(token=token or "", max_age=max_age, key="oj-session-cookie", default=None)
