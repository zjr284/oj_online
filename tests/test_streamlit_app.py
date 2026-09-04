"""Step 6 Streamlit 前端冒烟测试（离线：不依赖后端进程，仅验证页面结构与校验逻辑）。"""

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def test_login_page_renders_for_guest():
    """未登录：显示登录/注册导航，无异常。"""
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert not at.exception
    assert list(at.radio[0].options) == ["🔑 登录", "📝 注册"]


def test_login_empty_fields_shows_friendly_error():
    """空表单提交：本地校验拦截并友好提示（不发请求）。"""
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    at.button[0].click().run()
    assert not at.exception
    assert any("请输入用户名和密码" in str(e.value) for e in at.error)


def test_ai_page_renders_for_logged_in_user():
    """Advance R1：登录用户（含普通用户）可进入 AI 命题页，配置/任务表单齐全。

    后端未启动时页面仍可渲染（页面内对后端调用均容错）。
    """
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["me"] = {"username": "alice", "user_id": "2", "role": "user"}
    at.run()
    assert not at.exception
    assert "✨ AI 命题" in list(at.radio[0].options)   # 所有登录用户可见（非管理员专属）
    at.radio[0].set_value("✨ AI 命题").run()
    assert not at.exception
    texts = (" ".join(str(md.value) for md in at.markdown)
             + " ".join(str(s.value) for s in at.subheader)
             + " ".join(str(t.value) for t in at.title)
             + " ".join(str(e.label) for e in at.expander))
    assert "AI 智能命题" in texts
    assert "新建命题任务" in texts
    assert "模型配置" in texts
    assert "任务记录" in texts
    # 配置表单关键字段：提供商 URL / 模型 / 密钥 / 价格 / 计价单位
    labels = [ti.label for ti in at.text_input]
    assert any("提供商 URL" in l for l in labels)
    assert any("模型名称" in l for l in labels)
    assert any("模型密钥" in l for l in labels)
    assert any("输入价格" in l for l in labels)
    assert any("输出价格" in l for l in labels)
