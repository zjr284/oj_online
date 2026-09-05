"""根据课程文档补充的回归用例，重点覆盖原测试遗漏的跨模块行为。"""
import asyncio
import inspect
import json
import signal
import sys
import time
from datetime import datetime, timedelta

import httpx
import psutil
import pytest
from fastapi.routing import APIRoute
from sqlalchemy import select

from app import config
from app.core.security import hash_password, verify_password
from app.database import SessionLocal
from app.judge import runner
from app.main import app
from app.models import Session, Submission, User
from app.routers import submissions
from app.services import ai_service, judge_service
from app.services.problem_store import store
from conftest import login, wait_status
from test_ai import CONFIG, GENERATED, _config, _mock_model, _wait_task
from test_submissions import AC_CODE, PROBLEM, _setup, _submit


def test_all_business_routes_are_async():
    from app.routers import ai, auth, languages, logs, maintenance, problems, users
    routes = [route for module in (ai, auth, languages, logs, maintenance, problems, users, submissions)
              for route in module.router.routes if isinstance(route, APIRoute)]
    assert len(routes) >= 29
    assert all(inspect.iscoroutinefunction(route.endpoint) for route in routes)


@pytest.mark.parametrize('method,path', [
    ('POST', '/api/problems/'), ('POST', '/api/languages/'),
    ('POST', '/api/submissions/'), ('POST', '/api/users/admin'),
    ('PUT', '/api/users/2/role'), ('PUT', '/api/ai/model-config'),
    ('PUT', '/api/problems/%2E%2E/log_visibility'),
])
async def test_auth_before_malformed_json(client, method, path):
    response = await client.request(method, path, content='{bad',
                                    headers={'Content-Type': 'application/json'})
    assert response.status_code == 401
    assert response.json()['code'] == 401


async def test_admin_permission_before_path_and_body_validation(client):
    await client.post('/api/users/', json={'username': 'alice', 'password': 'secret1'})
    await login(client, 'alice', 'secret1')
    for method, path in [('DELETE', '/api/problems/%2E%2E'),
                         ('PUT', '/api/problems/%2E%2E/log_visibility'),
                         ('PUT', '/api/users/invalid/role')]:
        response = await client.request(method, path, content='{bad',
                                        headers={'Content-Type': 'application/json'})
        assert response.status_code == 403


async def test_visibility_cannot_be_changed_through_problem_edit(client):
    await _setup(client)
    await client.post('/api/users/', json={'username': 'alice', 'password': 'secret1'})
    await login(client, 'alice', 'secret1')
    response = await client.put('/api/problems/1002', json={**PROBLEM, 'public_cases': True})
    assert response.status_code == 403
    response = await client.post('/api/problems/', json={**PROBLEM, 'id': '2003', 'public_cases': True})
    assert response.status_code == 403
    await login(client, 'admin', 'admintestpassword')
    await client.put('/api/problems/1002/log_visibility', json={'public_cases': True})
    await login(client, 'alice', 'secret1')
    response = await client.put('/api/problems/1002', json={**PROBLEM, 'title': 'Edited'})
    assert response.status_code == 200
    assert (await client.get('/api/problems/1002')).json()['data']['public_cases'] is True
    response = await client.put('/api/problems/1002', json={**PROBLEM, 'public_cases': False})
    assert response.status_code == 403


async def test_omitted_limits_fall_back_without_changing_api_defaults(client):
    await _setup(client)
    problem = {k: v for k, v in PROBLEM.items() if k not in ('time_limit', 'memory_limit')}
    problem.update(id='2001', testcases=[{'input': '', 'output': '3'}])
    assert (await client.post('/api/problems/', json=problem)).status_code == 200
    response = await client.get('/api/problems/2001')
    assert response.json()['data']['time_limit'] == 3
    assert response.json()['data']['memory_limit'] == 128
    # 更新可见性不应把 API 默认值变成题目显式限制。
    await client.put('/api/problems/2001/log_visibility', json={'public_cases': True})
    await client.post('/api/languages/', json={
        'name': 'short', 'file_ext': '.py', 'run_cmd': 'python3 {src}',
        'time_limit': 0.15, 'memory_limit': 16})
    sid = await _submit(client, 'import time; time.sleep(0.4); print(3)', '2001', 'short')
    data = await wait_status(client, sid)
    assert data['status'] == 'success' and data['verdicts'] == {'TLE': 1}
    cfg = await store.get('2001', for_judge=True)
    engine = runner.JudgeRunner({'file_ext': '.py', 'time_limit': 2, 'memory_limit': 16},
                                cfg, config.DATA_DIR)
    assert engine.memory_limit == 16
    engine = runner.JudgeRunner({'file_ext': '.py', 'time_limit': 2, 'memory_limit': 16},
                                {'time_limit': 1, 'memory_limit': 64}, config.DATA_DIR)
    assert (engine.time_limit, engine.memory_limit) == (1, 64)


async def test_rejudge_clears_all_old_result_fields(client, monkeypatch):
    await _setup(client)
    sid = await _submit(client, AC_CODE)
    await wait_status(client, sid)
    monkeypatch.setattr(judge_service, 'schedule_judge', lambda _: None)
    response = await client.put(f'/api/submissions/{sid}/rejudge')
    assert response.status_code == 200
    detail = (await client.get(f'/api/submissions/{sid}')).json()['data']
    log = (await client.get(f'/api/submissions/{sid}/log')).json()['data']
    assert detail['status'] == 'pending' and detail['counts'] is None
    assert log == {'details': [], 'score': None, 'counts': None}


async def test_deleted_problem_does_not_erase_submission_log(client):
    await _setup(client)
    sid = await _submit(client, AC_CODE)
    await wait_status(client, sid)
    await client.delete('/api/problems/1002')
    response = await client.get(f'/api/submissions/{sid}/log')
    assert response.status_code == 200
    assert len(response.json()['data']['details']) == 4


async def test_wrong_answers_do_not_count_as_solved(client):
    await _setup(client)
    for code in ('print(999)', 'print(0)', AC_CODE, AC_CODE):
        sid = await _submit(client, code)
        assert (await wait_status(client, sid))['status'] == 'success'
        user = (await client.get('/api/users/1')).json()['data']
        assert user['resolve_count'] == (1 if code == AC_CODE else 0)
    assert user['submit_count'] == 4


async def test_expired_session_cannot_logout_successfully(client):
    await login(client, 'admin', 'admintestpassword')
    async with SessionLocal() as db:
        session = await db.get(Session, client.cookies['oj_session'])
        session.expires_at = datetime.now() - timedelta(seconds=1)
        await db.commit()
    assert (await client.post('/api/auth/logout')).status_code == 401


def test_long_passwords_do_not_share_a_truncated_hash():
    password = '密' * 30 + 'first'
    hashed = hash_password(password)
    assert verify_password(password, hashed)
    assert not verify_password('密' * 30 + 'other', hashed)


async def test_problem_read_guards_all_entry_points(client):
    await _setup(client)
    response = await client.post('/api/submissions/', json={
        'problem_id': '../outside', 'language': 'python', 'code': 'print(1)'})
    assert response.status_code == 400
    with pytest.raises(Exception) as exc:
        await store.get('../outside')
    assert exc.value.status == 400
    (config.PROBLEMS_DIR / '1002.json').write_text('{broken')
    response = await client.post('/api/submissions/', json={
        'problem_id': '1002', 'language': 'python', 'code': 'print(1)'})
    assert response.status_code == 500


@pytest.mark.parametrize('command', ['python3 "{src}', 'python3 {unknown} {src}',
                                    'python3 {src} && true', 'python3 {src}\x00'])
async def test_language_command_validation(client, command):
    await login(client, 'admin', 'admintestpassword')
    response = await client.post('/api/languages/', json={
        'name': 'bad', 'file_ext': '.py', 'run_cmd': command})
    assert response.status_code == 400


async def test_run_timeout_cleans_child_processes(tmp_path):
    engine = runner.JudgeRunner({'file_ext': '.py', 'run_cmd': 'python3 {src}'},
                                {'time_limit': 0.3}, tmp_path)
    code = "import subprocess, sys, time\np=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\nopen('child.pid','w').write(str(p.pid))\ntime.sleep(30)"
    await engine.compile(code)
    result = await asyncio.wait_for(engine.run_case({'input': '', 'output': ''}, 0), 3)
    assert result.result == 'TLE'
    pid = int((tmp_path / 'child.pid').read_text())
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE


async def test_compile_timeout_and_cancellation_cleanup(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'COMPILE_TIMEOUT', 0.3)
    language = {'file_ext': '.py', 'compile_cmd': 'python3 {src}', 'run_cmd': 'python3 {src}'}
    engine = runner.JudgeRunner(language, {}, tmp_path)
    code = "import os, time\nopen('compiler.pid','w').write(str(os.getpid()))\ntime.sleep(30)"
    success, message = await asyncio.wait_for(engine.compile(code), 3)
    assert not success and message == 'compile timeout'
    assert not psutil.pid_exists(int((tmp_path / 'compiler.pid').read_text()))
    monkeypatch.setattr(runner, 'COMPILE_TIMEOUT', 30)
    task = asyncio.create_task(engine.compile(code))
    await asyncio.sleep(0.15)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 3)
    assert not psutil.pid_exists(int((tmp_path / 'compiler.pid').read_text()))


async def test_output_is_bounded_and_sigkill_is_not_misclassified(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'MAX_OUTPUT_BYTES', 4096)
    engine = runner.JudgeRunner({'file_ext': '.py', 'run_cmd': 'python3 {src}'}, {}, tmp_path)
    await engine.compile("while True: print('x' * 1000)")
    result = await asyncio.wait_for(engine.run_case({'input': '', 'output': ''}, 0), 3)
    assert result.result == 'RE'
    await engine.compile('import os, signal; os.kill(os.getpid(), signal.SIGKILL)')
    result = await engine.run_case({'input': '', 'output': ''}, 0)
    assert result.result == 'RE'


async def test_ai_retry_and_invalid_output_keep_usage(client, monkeypatch):
    await _config(client)
    responses = iter(['', json.dumps(GENERATED)])
    async def fake(cfg, payload):
        return {'choices': [{'message': {'content': next(responses)}}],
                'usage': {'prompt_tokens': 100, 'completion_tokens': 200}}
    monkeypatch.setattr(ai_service, '_request_model', fake)
    tid = (await client.post('/api/ai/problem-tasks/', json={'requirement': 'test'})).json()['data']['task_id']
    task = await _wait_task(client, tid)
    assert task['usage']['total_tokens'] == 600
    assert task['usage']['cost'] == 0.0001
    assert len(task['usage']['calls']) == 2
    _mock_model(monkeypatch, content='not json')
    tid = (await client.post('/api/ai/problem-tasks/', json={'requirement': 'test'})).json()['data']['task_id']
    task = await _wait_task(client, tid)
    assert task['status'] == 'failed'
    assert task['usage']['total_tokens'] == 300


async def test_ai_sse_broadcast_and_stale_initial_state(client, monkeypatch):
    await _config(client)
    _mock_model(monkeypatch, delay=10)
    tid = (await client.post('/api/ai/problem-tasks/', json={'requirement': 'test'})).json()['data']['task_id']
    state = (await client.get(f'/api/ai/problem-tasks/{tid}')).json()['data']
    streams = [ai_service.sse_stream(state, tid) for _ in range(2)]
    for stream in streams:
        await anext(stream)
    response = await client.put(f'/api/ai/problem-tasks/{tid}/cancel')
    assert response.status_code == 200
    async def final(stream):
        async for frame in stream:
            if 'event: final' in frame:
                assert 'cancelled' in frame
                await stream.aclose()
                return
        pytest.fail('missing final event')
    await asyncio.wait_for(asyncio.gather(*(final(stream) for stream in streams)), 3)
    # 即便首个状态快照为 pending，订阅发生在终态之后也要立即结束。
    stream = ai_service.sse_stream(state, tid)
    assert 'cancelled' in await anext(stream)
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


async def test_ai_reference_id_and_total_timeout(client, monkeypatch):
    await _config(client)
    await client.post('/api/problems/', json=PROBLEM)
    _mock_model(monkeypatch)
    tid = (await client.post('/api/ai/problem-tasks/', json={
        'requirement': '改编', 'problem_id': '1002'})).json()['data']['task_id']
    task = await _wait_task(client, tid)
    assert task['result']['id'] == '1002'
    _mock_model(monkeypatch, delay=10)
    monkeypatch.setattr(ai_service, 'REQUEST_TIMEOUT', 0.15)
    tid = (await client.post('/api/ai/problem-tasks/', json={'requirement': 'test'})).json()['data']['task_id']
    task = await _wait_task(client, tid)
    assert task['status'] == 'failed' and 'timed out' in task['result']['error']


@pytest.mark.parametrize('field,value', [('model', '  '), ('api_key', '  '),
    ('provider_url', 'https://'), ('provider_url', 'https://user:secret@example.com/v1'),
    ('input_price', 'NaN'), ('output_price', 'Infinity')])
async def test_ai_config_invalid_values(client, field, value):
    await login(client, 'admin', 'admintestpassword')
    response = await client.put('/api/ai/model-config', json={**CONFIG, field: value})
    assert response.status_code == 400


async def test_reset_stops_tasks_and_clears_rate_limit(client, monkeypatch):
    from app.core.rate_limit import RateLimiter
    await _setup(client)
    await _config(client)
    _mock_model(monkeypatch, delay=30)
    monkeypatch.setattr(submissions, 'submit_limiter', RateLimiter(1, 60))
    await _submit(client, 'import time; time.sleep(20)')
    await client.post('/api/ai/problem-tasks/', json={'requirement': 'test'})
    assert (await client.post('/api/reset/')).status_code == 200
    assert not judge_service._judge_tasks and not ai_service._tasks
    assert not submissions.submit_limiter._hits
    assert not ai_service.CONFIG_PATH.exists()
    await login(client, 'admin', 'admintestpassword')
    await client.post('/api/problems/', json=PROBLEM)
    sid = await _submit(client, AC_CODE)
    assert (await wait_status(client, sid))['score'] == 40


async def test_legacy_results_normalized_without_rejudging(client):
    await _setup(client)
    async with SessionLocal() as db:
        db.add(Submission(user_id=1, problem_id='1002', language='python', code='x',
                          status='error', score=10, total_score=40, counts={'AC': 1, 'WA': 3}))
        await db.commit()
    await judge_service.normalize_legacy_results()
    data = (await client.get('/api/submissions/1')).json()['data']
    assert data['status'] == 'success' and data['score'] == 10 and data['counts'] == 40


async def test_ai_uses_config_snapshot(client, monkeypatch):
    await _config(client)
    original = ai_service._run_task
    entered, proceed = asyncio.Event(), asyncio.Event()
    received = []
    async def delayed(task_id, cfg):
        entered.set()
        await proceed.wait()
        await original(task_id, cfg)
    async def fake(cfg, payload):
        received.append((cfg['provider_url'], payload['model'], cfg['api_key']))
        return {'choices': [{'message': {'content': json.dumps(GENERATED)}}],
                'usage': {'prompt_tokens': 1, 'completion_tokens': 2}}
    monkeypatch.setattr(ai_service, '_run_task', delayed)
    monkeypatch.setattr(ai_service, '_request_model', fake)
    tid = (await client.post('/api/ai/problem-tasks/', json={'requirement': 'test'})).json()['data']['task_id']
    await asyncio.wait_for(entered.wait(), 2)
    await client.put('/api/ai/model-config', json={**CONFIG, 'model': 'different', 'api_key': 'new-key'})
    proceed.set()
    task = await _wait_task(client, tid)
    assert task['model'] == CONFIG['model']
    assert received == [(CONFIG['provider_url'], CONFIG['model'], CONFIG['api_key'])]


async def test_model_accepts_base_url_and_sanitizes_before_truncating(monkeypatch):
    received = []
    secret = 's' * 250
    class FakeClient:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, **kwargs):
            received.append(url)
            return httpx.Response(500, text='bad key: ' + secret)
    monkeypatch.setattr(ai_service.httpx, 'AsyncClient', FakeClient)
    with pytest.raises(RuntimeError) as exc:
        await ai_service._request_model({'provider_url': 'https://example.com/v1', 'api_key': secret}, {})
    assert received == ['https://example.com/v1/chat/completions']
    assert 's' * 10 not in str(exc.value) and '***' in str(exc.value)
