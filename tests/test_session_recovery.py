import importlib.util
from pathlib import Path

import pytest
import requests

from agent_reach.backends import browser_ready

spec = importlib.util.spec_from_file_location('wr_recovery', Path(__file__).parents[1] / 'agent_reach/collection/weread_helper/weread_client.py')
wr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wr)


def client(tmp_path):
    c = wr.WeReadClient(tmp_path / 'login.json')
    c.vid = 'fixture'
    c.session.cookies.set('wr_skey', 'old', domain='.weread.qq.com', path='/')
    return c


def test_rotation_used_without_renewal(tmp_path, monkeypatch):
    c = client(tmp_path)
    calls = []

    def fake(method, path, **kwargs):
        calls.append(path)
        if path == '/api/userInfo':
            c.session.cookies.set('wr_skey', 'new', domain='weread.qq.com', path='/')
            c.normalize_cookies()
            return {'name': 'fixture'}, {}, []
        assert c.session.cookies.get_dict()['wr_skey'] == 'new'
        return {'reviews': []}, {}, []

    monkeypatch.setattr(c, '_request', fake)
    c.prepare()
    c.catalog('MP_WXS_123')
    assert calls == ['/api/userInfo', '/web/mp/articles']
    req = c.session.prepare_request(requests.Request('GET', wr.BASE))
    assert req.headers['Cookie'].count('wr_skey=') == 1
    reloaded = wr.WeReadClient(c.auth_path)
    assert reloaded.load()
    assert reloaded.session.cookies.get_dict()['wr_skey'] == 'new'


def test_failed_renewal_validation_preserves_disk(tmp_path, monkeypatch):
    c = client(tmp_path)
    c.validated = True
    c.save()
    before = c.auth_path.read_bytes()

    def fake(method, path, **kwargs):
        if path == '/web/login/renewal':
            c.session.cookies.set('wr_skey', 'bad', domain='weread.qq.com')
            return {'succ': True}, {'x-wr-ticket': 'fixture'}, []
        raise wr.WeReadError('access_denied', 'denied')

    monkeypatch.setattr(c, '_request', fake)
    with pytest.raises(wr.WeReadError):
        c.renew()
    assert c.auth_path.read_bytes() == before
    assert c.session.cookies.get_dict()['wr_skey'] == 'old'
    assert not c.extra_headers


@pytest.mark.parametrize('code,expected', [('-2041', 2), ('access_denied', 1), ('rate_limited', 1), ('verification_required', 1)])
def test_bounded_recovery(tmp_path, monkeypatch, code, expected):
    c = client(tmp_path)
    calls = []

    def fake(method, path, **kwargs):
        calls.append(path)
        if path == '/api/userInfo':
            return {'name': 'fixture'}, {}, []
        raise wr.WeReadError(code, 'fixture')

    monkeypatch.setattr(c, '_request', fake)
    with pytest.raises(wr.WeReadError):
        c.catalog('MP_WXS_123')
    assert calls.count('/web/mp/articles') == expected
    assert '/web/login/renewal' not in calls


def test_profile_never_borrows_other_connection(monkeypatch):
    monkeypatch.setattr(browser_ready, '_fetch_daemon_status', lambda **kw: {'extensionConnected': True, 'contextId': 'another'})
    assert browser_ready.prepare('expected')['status'] == 'needs_browser'


def test_ready_never_restarts_active_daemon(monkeypatch):
    monkeypatch.setattr(browser_ready, '_fetch_daemon_status', lambda **kw: {'extensionConnected': True, 'contextId': 'expected', 'pending': 2})
    monkeypatch.setattr(browser_ready.subprocess, 'run', lambda *a, **k: pytest.fail('no restart'))
    assert browser_ready.prepare('expected')['pending'] == 2


def test_missing_daemon_startup(monkeypatch):
    states = iter([None, {'extensionConnected': True, 'contextId': 'expected'}])
    monkeypatch.setattr(browser_ready, '_fetch_daemon_status', lambda **kw: next(states))
    calls = []
    monkeypatch.setattr(browser_ready.subprocess, 'run', lambda *a, **k: calls.append(a[0]))
    assert browser_ready.prepare('expected')['status'] == 'ready'
    assert calls == [['opencli', 'doctor']]

@pytest.mark.parametrize('pending,unknown,expected', [
    (0, 0, 'ready'), (2, 0, 'busy'), (0, 1, 'needs_result_check'),
    (2, 1, 'needs_result_check'),
])
def test_preflight_does_not_allow_replay_of_unknown_or_running_commands(monkeypatch, pending, unknown, expected):
    monkeypatch.setattr(browser_ready, '_fetch_daemon_status', lambda **kw: {
        'extensionConnected': True, 'contextId': 'expected',
        'pending': pending, 'commandResultUnknown': unknown})
    monkeypatch.setattr(browser_ready.subprocess, 'run', lambda *a, **kw: pytest.fail('do not restart active daemon'))
    result = browser_ready.prepare('expected')
    assert result['status'] == expected
    assert result['pending'] == pending
    assert result['command_result_unknown'] == unknown
