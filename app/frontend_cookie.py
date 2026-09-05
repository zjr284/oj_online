"""同步浏览器会话 Cookie，并将浏览器前进/后退事件转成 Streamlit rerun。"""
from pathlib import Path

import streamlit.components.v1 as components

_component = components.declare_component(
    "oj_session_cookie",
    path=str(Path(__file__).with_name("static") / "session_cookie"),
)


def write_session_cookie(token: str | None, max_age: int) -> None:
    """写入/删除 Cookie，并挂载不会销毁当前会话的历史导航监听器。"""
    _component(token=token or "", max_age=max_age, key="oj-session-cookie", default=None)
