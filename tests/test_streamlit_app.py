"""Step 6 Streamlit 前端冒烟测试（离线：不依赖后端进程，仅验证页面结构与校验逻辑）。"""

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def _registered_page_titles(at):
    return [page["page_name"] for page in at._registered_pages.values()]


def _open_page(at, url_path, **query_params):
    """在 AppTest 中打开 st.navigation 注册的 callable 页面。"""
    at._page_hash = next(
        page_hash for page_hash, page in at._registered_pages.items()
        if page["url_pathname"] == url_path
    )
    at.query_params.clear()
    at.query_params.update(query_params)
    return at.run()


def test_login_page_renders_for_guest():
    """未登录：显示登录/注册导航，无异常。"""
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert not at.exception
    assert _registered_page_titles(at) == ["登录", "注册"]


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
    assert "访问审计" not in _registered_page_titles(at)

    # 管理员：导航可见，页面渲染出筛选表单与分页控件
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["me"] = {"username": "root", "user_id": "1", "role": "admin"}
    at.run()
    assert not at.exception
    assert "访问审计" in _registered_page_titles(at)
    _open_page(at, "audit")
    assert not at.exception
    texts = (" ".join(str(md.value) for md in at.markdown)
             + " ".join(str(t.value) for t in at.title)
             + " ".join(str(e.value) for e in at.error))
    assert "访问审计" in texts
    labels = [ti.label for ti in at.text_input]
    assert any("用户名" in l for l in labels)
    assert any("题目 ID" in l for l in labels)


def test_refresh_restore_rejects_forged_token(monkeypatch):
    """刷新恢复登录态：URL 参数带伪造 token（或后端不可达）时，安全回到未登录而非报错。"""
    monkeypatch.setenv("OJ_API_BASE", "http://127.0.0.1:1")
    at = AppTest.from_file(APP, default_timeout=30)
    at.query_params["oj_s"] = "forged-token"
    at.query_params["oj_u"] = "1"
    at.run()
    assert not at.exception
    assert _registered_page_titles(at) == ["登录", "注册"]


def test_streamlit_navigation_switches_page_without_login_loss(monkeypatch):
    """回归：Streamlit 原生导航切页后保留当前登录会话。"""
    monkeypatch.setenv("OJ_API_BASE", "http://127.0.0.1:1")
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["me"] = {"username": "alice", "user_id": "2", "role": "user"}
    at.run()
    assert not at.exception
    assert any(str(title.value) == "📋 题目列表" for title in at.title)

    _open_page(at, "submissions")
    assert not at.exception
    assert at.session_state["me"]["username"] == "alice"
    assert any(str(title.value) == "📜 评测记录" for title in at.title)

    _open_page(at, "profile")
    assert not at.exception
    assert at.session_state["me"]["username"] == "alice"
    assert any(str(title.value) == "🙍 个人主页" for title in at.title)


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
    assert "AI 命题" in _registered_page_titles(at)
    assert "语言管理" in _registered_page_titles(at)
    _open_page(at, "ai")
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
        elif path == '/api/ai/problem-tasks/7/retry' and method == 'POST':
            state['retried'] = True
            data = {'task_id': 8, 'status': 'pending', 'retried_from': 7}
        elif path == '/api/ai/problem-tasks/8':
            data = {'task_id': 8, 'status': 'pending', 'progress': 0,
                    'requirement': '测试命题', 'model': 'example', 'result': None,
                    'usage': None}
        elif path == '/api/ai/problem-tasks/7':
            from test_ai import GENERATED
            data = {'task_id': 7, 'status': state.get('status', 'running'), 'progress': 0.4,
                    'requirement': '测试命题', 'model': 'example', 'result': GENERATED,
                    'usage': {'input_tokens': 100, 'output_tokens': 20, 'total_tokens': 120,
                              'cost': 0.1, 'currency': 'CNY', 'price_source': 'provider'}}
        elif path == '/api/logs/access/':
            rows = state.get('audit_logs', [])
            params = kwargs.get('params') or {}
            page = int(params.get('page', 1))
            page_size = int(params.get('page_size', len(rows) or 1))
            start = (page - 1) * page_size
            data = rows[start:start + page_size]
        elif path.startswith('/api/logs/access/') and method == 'DELETE':
            log_id = path.rsplit('/', 1)[-1]
            state['audit_logs'] = [
                row for row in state.get('audit_logs', []) if str(row['log_id']) != log_id
            ]
            data = {'log_id': log_id}
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
    at.run()
    _open_page(at, 'languages')

    assert not at.exception
    assert '语言管理' in _registered_page_titles(at)
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


def test_audit_rows_show_details_and_delete_independently(monkeypatch):
    state = {'audit_logs': [{
        'log_id': '41', 'username': 'alice', 'user_id': '99', 'problem_id': '1001',
        'action': 'view_logs', 'status': '200', 'time': '2026-09-06 10:00:00',
    }, {
        'log_id': '42', 'username': 'bob', 'user_id': '100', 'problem_id': '1002',
        'action': 'view_logs', 'status': '403', 'time': '2026-09-06 10:01:00',
    }]}
    _fake_api(monkeypatch, state)
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state['me'] = {'username': 'root', 'user_id': '1', 'role': 'admin'}
    at.run()
    _open_page(at, 'audit')

    assert {'alice', 'bob'} <= {str(item.value) for item in at.text}
    assert [button.label for button in at.button].count('查看详情') == 2
    assert [button.label for button in at.button].count('删除') == 2

    # 每条日志末尾的详情按钮只展示对应记录，不发生删除。
    next(button for button in at.button if button.label == '查看详情').click().run()
    detail_text = {str(item.value) for item in at.text}
    assert {'41', 'alice', '1001', '查看评测日志'} <= detail_text
    assert not any(method == 'DELETE' for method, _path, _body in state['requests'])

    # 点击第二条记录的删除按钮，经二次确认后只删第二条。
    _open_page(at, 'audit')  # 关闭上方详情弹窗
    delete_buttons = [button for button in at.button if button.label == '删除']
    delete_buttons[1].click().run()
    next(button for button in at.button if button.label == '确认删除').click().run()

    assert not at.exception
    assert [row['log_id'] for row in state['audit_logs']] == ['41']
    assert any(method == 'DELETE' and path == '/api/logs/access/42'
               for method, path, _body in state['requests'])


def test_audit_pagination_does_not_skip_the_twenty_first_log(monkeypatch):
    state = {'audit_logs': [
        {'log_id': str(index), 'username': f'user{index}', 'user_id': str(index),
         'problem_id': '1001', 'action': 'view_logs', 'status': '200',
         'time': '2026-09-06 10:00:00'}
        for index in range(1, 22)
    ]}
    _fake_api(monkeypatch, state)
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state['me'] = {'username': 'root', 'user_id': '1', 'role': 'admin'}
    at.run()
    _open_page(at, 'audit')
    first_page = {str(item.value) for item in at.text}
    assert 'user20' in first_page and 'user21' not in first_page

    next(button for button in at.button if button.label == '下一页').click().run()
    second_page = {str(item.value) for item in at.text}
    assert 'user21' in second_page and 'user20' not in second_page


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

    next(button for button in at.button if button.label == '← 返回列表').click().run()
    assert not at.exception
    assert at.session_state['me']['username'] == 'alice'
    assert any(str(title.value) == '📋 题目列表' for title in at.title)


def test_only_admin_can_set_problem_log_visibility(monkeypatch):
    state = {}
    _fake_api(monkeypatch, state)

    user_app = AppTest.from_file(APP, default_timeout=30)
    user_app.session_state['me'] = {'username': 'alice', 'user_id': '2', 'role': 'user'}
    user_app.run()
    _open_page(user_app, 'problem_detail', problem='1001')
    assert not any(radio.label == '谁可以查看日志详情' for radio in user_app.radio)

    admin_app = AppTest.from_file(APP, default_timeout=30)
    admin_app.session_state['me'] = {'username': 'root', 'user_id': '1', 'role': 'admin'}
    admin_app.run()
    _open_page(admin_app, 'problem_detail', problem='1001')
    setting = next(radio for radio in admin_app.radio if radio.label == '谁可以查看日志详情')
    setting.set_value(True).run()
    next(button for button in admin_app.button if button.label == '保存日志权限设置').click().run()
    request = next(item for item in state['requests']
                   if item[0] == 'PUT' and item[1] == '/api/problems/1001/log_visibility')
    assert request[2] == {'public_cases': True}


def test_native_route_restores_previous_interface(monkeypatch):
    """Streamlit 原生页面路由与查询参数可恢复对应界面。"""
    _fake_api(monkeypatch, {})
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state['me'] = {'username': 'alice', 'user_id': '2', 'role': 'user'}
    at.run()
    _open_page(at, 'profile')
    assert any(str(title.value) == '🙍 个人主页' for title in at.title)

    _open_page(at, 'problem_detail', problem='1001')
    assert at.session_state['prob_view'] == 'detail'
    assert any(str(title.value) == 'A + B Problem' for title in at.title)

    # 模拟浏览器“返回”恢复之前的 Streamlit 页面。
    _open_page(at, 'profile')
    assert at.session_state['me']['username'] == 'alice'
    assert any(str(title.value) == '🙍 个人主页' for title in at.title)


def test_navigation_uses_only_streamlit_router():
    """页面导航交给 Streamlit，Cookie 组件不再干预浏览器历史。"""
    component = (Path(APP).parent / 'app' / 'static' / 'session_cookie' / 'index.html').read_text()
    app_source = Path(APP).read_text()
    assert 'st.navigation(' in app_source
    assert 'st.page_link(' in app_source
    assert 'st.switch_page(' in app_source
    assert 'st.link_button(' not in app_source
    assert 'popstate' not in component and 'location.reload' not in component


def test_session_cookie_is_confirmed_before_refresh():
    """登录后先落盘浏览器 Cookie，再通知 Streamlit 完成一次会话同步。"""
    component = (Path(APP).parent / 'app' / 'static' / 'session_cookie' / 'index.html').read_text()
    assert 'window.parent.document' in component
    assert 'SameSite=Lax' in component
    assert 'streamlit:setComponentValue' in component
    assert 'oj_browser_session' in component
    assert 'cookieValue(cookieDocument, COOKIE_NAME) === encoded' in component
    assert 'args.clear && (current !== null || legacyCurrent !== null)' in component


def test_profile_can_rename_current_user(monkeypatch):
    state = {}
    _fake_api(monkeypatch, state)
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state['me'] = {'username': 'alice', 'user_id': '2', 'role': 'user'}
    at.run()
    _open_page(at, 'profile')

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
    at.run()
    _open_page(at, 'ai_task', task='7')
    assert not at.exception
    assert not any('http-equiv="refresh"' in str(md.value) for md in at.markdown)
    assert any('中断任务' in b.label for b in at.button)
    assert any('Token' in m.label for m in at.metric)
    next(b for b in at.button if '中断任务' in b.label).click().run()
    assert not at.exception
    assert state['status'] == 'cancelled'
    assert at.session_state['ai_task_id'] == 7
    assert any('已中断' in str(info.value) for info in at.info)


def test_failed_ai_task_can_restart_as_a_new_task(monkeypatch):
    state = {'status': 'failed'}
    _fake_api(monkeypatch, state)
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state['me'] = {'username': 'root', 'user_id': '1', 'role': 'admin'}
    at.run()
    _open_page(at, 'ai_task', task='7')

    restart = next(button for button in at.button if button.label == '🔄 重新开始此任务')
    restart.click().run()

    assert not at.exception
    assert state['retried'] is True
    assert any(method == 'POST' and path == '/api/ai/problem-tasks/7/retry'
               for method, path, _body in state['requests'])
    assert at.session_state['ai_task_id'] == 8
    route_task = at.query_params['task']
    assert route_task == '8' or route_task == ['8']
    assert any('已从任务 #7 创建新任务 #8' in str(item.value)
               for item in at.success)


def test_ai_result_can_be_reviewed_before_import(monkeypatch):
    _fake_api(monkeypatch, {'status': 'done'})
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state['me'] = {'username': 'root', 'user_id': '1', 'role': 'admin'}
    at.run()
    _open_page(at, 'ai_task', task='7')
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
    at.run()
    _open_page(at, 'submission_detail', submission='1')
    assert not at.exception
    assert any('未通过' in str(w.value) for w in at.warning)
    assert any('编译成功' in str(md.value) for md in at.markdown)
    assert not any('编译错误' in str(md.value) for md in at.markdown)
