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


def test_audit_page_admin_only_and_renders(monkeypatch):
    """Step 5：访问审计导航仅管理员可见；页面可渲染（后端未启动时容错报错）。

    OJ_API_BASE 指向死端口隔离真实后端，避免 live 401 清空会话影响断言。
    """
    monkeypatch.setenv("OJ_API_BASE", "http://127.0.0.1:1")
    # 普通用户：导航中不出现访问审计
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["me"] = {"username": "alice", "user_id": "2", "role": "user"}
    at.run()
    assert not at.exception
    assert "🛡 访问审计" not in list(at.radio[0].options)

    # 管理员：导航可见，页面渲染出筛选表单与分页控件
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["me"] = {"username": "root", "user_id": "1", "role": "admin"}
    at.run()
    assert not at.exception
    options = list(at.radio[0].options)
    assert "🛡 访问审计" in options
    at.radio[0].set_value("🛡 访问审计").run()
    assert not at.exception
    texts = (" ".join(str(md.value) for md in at.markdown)
             + " ".join(str(t.value) for t in at.title)
             + " ".join(str(e.value) for e in at.error))
    assert "访问审计" in texts
    labels = [ti.label for ti in at.text_input]
    assert any("用户 ID" in l for l in labels)
    assert any("题目 ID" in l for l in labels)


def test_refresh_restore_rejects_forged_token(monkeypatch):
    """刷新恢复登录态：URL 参数带伪造 token（或后端不可达）时，安全回到未登录而非报错。"""
    monkeypatch.setenv("OJ_API_BASE", "http://127.0.0.1:1")
    at = AppTest.from_file(APP, default_timeout=30)
    at.query_params["oj_s"] = "forged-token"
    at.query_params["oj_u"] = "1"
    at.run()
    assert not at.exception
    assert list(at.radio[0].options) == ["🔑 登录", "📝 注册"]


def test_sidebar_nav_switches_page_on_single_click(monkeypatch):
    """回归：侧边栏导航单击即切换页面且不回跳。

    曾因每次 rerun 给 st.radio 传 index=旧值覆盖用户刚点的选项（streamlit#3534），
    导致第一次点击被吞、需双击。修复后导航值由 key 绑定，一次点击即生效。
    """
    monkeypatch.setenv("OJ_API_BASE", "http://127.0.0.1:1")
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["me"] = {"username": "alice", "user_id": "2", "role": "user"}
    at.run()
    assert not at.exception
    assert at.radio[0].value == "📋 题目"   # 默认页

    at.radio[0].set_value("📜 评测记录").run()   # 单击一次
    assert not at.exception
    assert at.radio[0].value == "📜 评测记录"    # 选项不回跳
    assert at.session_state["nav"] == "📜 评测记录"

    at.radio[0].set_value("🙍 个人主页").run()   # 连续切换，每次都是一次点击
    assert not at.exception
    assert at.radio[0].value == "🙍 个人主页"
    assert at.session_state["nav"] == "🙍 个人主页"


def test_ai_and_language_pages_are_available_to_regular_users(monkeypatch):
    """普通用户可以进入 AI 命题和语言管理页面。

    后端未启动时页面仍可渲染（页面内对后端调用均容错）；
    OJ_API_BASE 指向死端口隔离真实后端。
    """
    monkeypatch.setenv("OJ_API_BASE", "http://127.0.0.1:1")
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["me"] = {"username": "alice", "user_id": "2", "role": "user"}
    at.run()
    assert not at.exception
    assert "✨ AI 命题" in list(at.radio[0].options)
    assert "🧩 语言管理" in list(at.radio[0].options)
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


def _fake_api(monkeypatch, state):
    import httpx
    import json

    def request(method, url, **kwargs):
        path = httpx.URL(url).path
        state.setdefault('requests', []).append((method, path, kwargs.get('json')))
        headers = {}
        if path == '/api/auth/login':
            data = {'username': 'alice', 'user_id': '2', 'role': 'user'}
            headers = {'set-cookie': 'oj_session=private-session-token; HttpOnly; Path=/'}
        elif path == '/api/auth/me':
            data = {'username': state.get('username', 'alice'), 'user_id': '2', 'role': 'user'}
        elif path == '/api/problems/':
            data = state.get('problems', [])
        elif path == '/api/problems/1001':
            data = {
                'id': '1001', 'title': 'A + B Problem',
                'description': '计算两个整数之和。',
                'input_description': '两个整数。', 'output_description': '整数之和。',
                'samples': [{'input': '1 2', 'output': '3'}],
                'constraints': '整数范围内', 'testcases': [{'input': '1 2', 'output': '3'}],
                'time_limit': 1, 'memory_limit': 64, 'tags': [],
            }
        elif path == '/api/users/2':
            data = {
                'user_id': '2', 'username': state.get('username', 'alice'), 'role': 'user',
                'join_time': '2026-01-01', 'submit_count': 0, 'resolve_count': 0,
            }
        elif path == '/api/users/2/username' and method == 'PUT':
            state['username'] = kwargs['json']['username']
            data = {'user_id': '2', 'username': state['username']}
        elif path == '/api/languages/':
            languages = state.setdefault('languages', ['python', 'cpp'])
            if method == 'POST':
                languages.append(kwargs['json']['name'])
                data = {'name': kwargs['json']['name']}
            else:
                data = {'name': languages}
        elif path == '/api/ai/problem-tasks/7/cancel':
            state['status'] = 'cancelled'
            data = {'task_id': 7, 'status': 'cancelled'}
        elif path == '/api/ai/problem-tasks/7':
            from test_ai import GENERATED
            data = {'task_id': 7, 'status': state.get('status', 'running'), 'progress': 0.4,
                    'requirement': '测试命题', 'model': 'example', 'result': GENERATED,
                    'usage': {'input_tokens': 100, 'output_tokens': 20, 'total_tokens': 120,
                              'cost': 0.1, 'currency': 'CNY', 'price_source': 'provider'}}
        elif path.endswith('/log'):
            data = {'score': 10, 'counts': 40}
        elif path == '/api/submissions/1':
            data = {'submission_id': '1', 'status': 'success', 'score': 10, 'counts': 40,
                    'compile_info': {'result': 'success', 'message': ''},
                    'run_info': {'result': 'finished', 'message': '4 test cases finished'},
                    'verdicts': {'AC': 1, 'WA': 3}}
        else:
            data = {}
        return httpx.Response(200, json={'code': 200, 'msg': 'success', 'data': data},
                              headers=headers, request=httpx.Request(method, url))
    monkeypatch.setattr(httpx, 'request', request)


def test_language_page_lists_and_registers_for_regular_user(monkeypatch):
    state = {'languages': ['python', 'cpp']}
    _fake_api(monkeypatch, state)
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state['me'] = {'username': 'alice', 'user_id': '2', 'role': 'user'}
    at.session_state['nav'] = '🧩 语言管理'
    at.run()

    assert not at.exception
    assert '🧩 语言管理' in list(at.radio[0].options)
    assert any('当前支持的语言' in str(s.value) for s in at.subheader)
    inputs = {item.label: item for item in at.text_input}
    inputs['语言名称'].set_value('go')
    inputs['源码扩展名'].set_value('.go')
    inputs['编译命令（解释型语言可留空）'].set_value('go build -o {exe} {src}')
    inputs['运行命令'].set_value('{exe}')
    next(button for button in at.button if button.label == '注册语言').click().run()

    assert not at.exception
    assert state['languages'] == ['python', 'cpp', 'go']
    request = next(item for item in state['requests']
                   if item[0] == 'POST' and item[1] == '/api/languages/')
    assert request[2] == {
        'name': 'go', 'file_ext': '.go',
        'compile_cmd': 'go build -o {exe} {src}', 'run_cmd': '{exe}',
    }


def test_successful_login_does_not_put_credentials_in_url(monkeypatch):
    _fake_api(monkeypatch, {})
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.text_input[0].set_value('alice')
    at.text_input[1].set_value('secret1')
    at.button[0].click().run()
    assert not at.exception
    assert at.session_state['me']['username'] == 'alice'
    assert at.session_state['cookies']['oj_session'] == 'private-session-token'
    assert 'oj_s' not in at.query_params and 'oj_u' not in at.query_params


def test_problem_title_is_the_detail_link(monkeypatch):
    state = {'problems': [{'id': '1001', 'title': 'A + B Problem'}]}
    _fake_api(monkeypatch, state)
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state['me'] = {'username': 'alice', 'user_id': '2', 'role': 'user'}
    at.run()

    title_button = next(button for button in at.button if button.label == 'A + B Problem')
    title_button.click().run()
    assert at.session_state['me']['username'] == 'alice'
    assert at.session_state['prob_view'] == 'detail'
    assert at.session_state['prob_id'] == '1001'
    route_problem = at.query_params['problem']
    assert route_problem == '1001' or route_problem == ['1001']
    assert not any(button.label in ('查看题目详情', '打开详情') for button in at.button)


def test_only_admin_can_set_problem_log_visibility(monkeypatch):
    state = {}
    _fake_api(monkeypatch, state)

    user_app = AppTest.from_file(APP, default_timeout=30)
    user_app.session_state['me'] = {'username': 'alice', 'user_id': '2', 'role': 'user'}
    user_app.query_params.update({'page': 'problems', 'problem': '1001'})
    user_app.run()
    assert not any(radio.label == '谁可以查看日志详情' for radio in user_app.radio)

    admin_app = AppTest.from_file(APP, default_timeout=30)
    admin_app.session_state['me'] = {'username': 'root', 'user_id': '1', 'role': 'admin'}
    admin_app.query_params.update({'page': 'problems', 'problem': '1001'})
    admin_app.run()
    setting = next(radio for radio in admin_app.radio if radio.label == '谁可以查看日志详情')
    setting.set_value(True).run()
    next(button for button in admin_app.button if button.label == '保存日志权限设置').click().run()
    request = next(item for item in state['requests']
                   if item[0] == 'PUT' and item[1] == '/api/problems/1001/log_visibility')
    assert request[2] == {'public_cases': True}


def test_url_route_restores_previous_interface(monkeypatch):
    """URL 是页面状态源，浏览器前进/后退后的 rerun 可恢复对应界面。"""
    _fake_api(monkeypatch, {})
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state['me'] = {'username': 'alice', 'user_id': '2', 'role': 'user'}
    at.query_params['page'] = 'profile'
    at.run()
    assert at.session_state['nav'] == '🙍 个人主页'
    assert any(str(title.value) == '🙍 个人主页' for title in at.title)

    at.query_params.clear()
    at.query_params.update({'page': 'problems', 'problem': '1001'})
    at.run()
    assert at.session_state['nav'] == '📋 题目'
    assert at.session_state['prob_view'] == 'detail'
    assert any(str(title.value) == 'A + B Problem' for title in at.title)

    # 模拟浏览器“返回”恢复之前的 URL。
    at.query_params.clear()
    at.query_params.update({'page': 'profile'})
    at.run()
    assert at.session_state['nav'] == '🙍 个人主页'
    assert any(str(title.value) == '🙍 个人主页' for title in at.title)


def test_browser_history_bridge_preserves_streamlit_session():
    """浏览器返回时应在当前 Streamlit 会话中 rerun，不得整页刷新丢失登录态。"""
    component = (Path(APP).parent / 'app' / 'static' / 'session_cookie' / 'index.html').read_text()
    assert 'type: "streamlit:setComponentValue"' in component
    assert 'window.parent.location.reload()' not in component


def test_profile_can_rename_current_user(monkeypatch):
    state = {}
    _fake_api(monkeypatch, state)
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state['me'] = {'username': 'alice', 'user_id': '2', 'role': 'user'}
    at.session_state['nav'] = '🙍 个人主页'
    at.run()

    rename = next(item for item in at.text_input if item.label.startswith('新用户名'))
    rename.set_value('alice_new')
    next(button for button in at.button if button.label == '保存用户名').click().run()
    assert not at.exception
    assert state['username'] == 'alice_new'
    assert at.session_state['me']['username'] == 'alice_new'


def test_ai_progress_and_cancel_keep_current_page(monkeypatch):
    state = {}
    _fake_api(monkeypatch, state)
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state['me'] = {'username': 'root', 'user_id': '1', 'role': 'admin'}
    at.session_state['nav'] = '✨ AI 命题'
    at.session_state['ai_view'] = 'task'
    at.session_state['ai_task_id'] = 7
    at.run()
    assert not at.exception
    assert not any('http-equiv="refresh"' in str(md.value) for md in at.markdown)
    assert any('中断任务' in b.label for b in at.button)
    assert any('Token' in m.label for m in at.metric)
    next(b for b in at.button if '中断任务' in b.label).click().run()
    assert not at.exception
    assert state['status'] == 'cancelled'
    assert at.session_state['ai_task_id'] == 7
    assert at.session_state['nav'] == '✨ AI 命题'
    assert any('已中断' in str(info.value) for info in at.info)


def test_ai_result_can_be_reviewed_before_import(monkeypatch):
    _fake_api(monkeypatch, {'status': 'done'})
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state['me'] = {'username': 'root', 'user_id': '1', 'role': 'admin'}
    at.session_state['nav'] = '✨ AI 命题'
    at.session_state['ai_view'] = 'task'
    at.session_state['ai_task_id'] = 7
    at.run()
    assert not at.exception
    editor = next(area for area in at.text_area if '审阅' in area.label)
    editor.set_value('not json')
    next(b for b in at.button if '保存为新题目' in b.label).click().run()
    assert not at.exception
    assert any('JSON 格式错误' in str(e.value) for e in at.error)


def test_submission_shows_partial_score_and_successful_compile(monkeypatch):
    _fake_api(monkeypatch, {})
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state['me'] = {'username': 'alice', 'user_id': '2', 'role': 'user'}
    at.session_state['nav'] = '📜 评测记录'
    at.session_state['sub_view'] = 'detail'
    at.session_state['sub_id'] = '1'
    at.run()
    assert not at.exception
    assert any('未通过' in str(w.value) for w in at.warning)
    assert any('编译成功' in str(md.value) for md in at.markdown)
    assert not any('编译错误' in str(md.value) for md in at.markdown)
