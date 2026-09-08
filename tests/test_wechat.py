"""Regression coverage for truthful Sogou listing results and optional runtime."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import requests

from agent_reach import cli, wechat
from agent_reach.channels.wechat import WeChatChannel

CARDS = """<html><title>搜狗微信</title><ul class="news-list">
<li><h3><a href="/link?url=one">第一篇</a></h3><p class="txt-info">第一篇摘要</p>
<div class="s-p"><span class="all-time-y2">公众号甲</span></div></li>
<li><h3><a href="/link?url=two">第二篇</a></h3><p class="txt-info">第二篇摘要</p>
<div class="s-p"><a class="account">公众号乙</a>
<span class="s2"><script>document.write(timeConvert('1735662600'))</script></span>
</div></li></ul></html>"""


@pytest.fixture
def parser_ready(monkeypatch):
    runtime = os.environ.get('AGENT_REACH_TEST_WECHAT_RUNTIME')
    if not runtime:
        pytest.skip('Set AGENT_REACH_TEST_WECHAT_RUNTIME to an installed parser dependency directory')
    monkeypatch.setattr(wechat, 'runtime_dir', lambda: Path(runtime))
    assert wechat.probe_runtime(), 'Configured parser runtime is broken'


@pytest.mark.parametrize("html, reason", [
    ('<title>搜狗搜索验证码</title>', 'blocked'),
    ('<title>微信搜索</title><input id="seccodeInput">', 'blocked'),
    ('<title>未知错误</title><body>请稍后再试</body>', 'unexpected_page'),
    ('<ul class="news-list"></ul>', 'unexpected_page'),
    (CARDS.replace('/link?url=one', 'https://example.com/article'), 'unexpected_link'),
])
def test_parser_failure_is_not_empty(parser_ready, html, reason):
    result = wechat._run_parser(html)
    assert result.returncode == 1
    assert reason in result.stderr
    assert not result.stdout


def test_actual_parser_keeps_fields_on_their_cards(parser_ready, monkeypatch):
    monkeypatch.setattr(wechat, '_fetch_listing', lambda query, page: CARDS)
    result = wechat.search_wechat('人工智能')
    first, second = result['articles']
    assert result['backend'] == 'wechat-article-search'
    assert result['body_fetched'] is False
    assert first['source'] == '公众号甲'
    assert first['summary'] == '第一篇摘要'
    assert first['datetime'] is None
    assert second['source'] == '公众号乙'
    # The timestamp is 2024-12-31 16:30 UTC = 2025-01-01 00:30 China time.
    assert second['datetime'] == '2025-01-01 00:30:00'
    assert second['date_text'] == '2025年01月01日'
    assert first['url'] == 'https://weixin.sogou.com/link?url=one'


def test_explicit_empty_result(parser_ready):
    result = wechat._run_parser('<div class="no-result">没有找到相关结果</div>')
    assert result.returncode == 0
    assert json.loads(result.stdout) == []


def test_missing_runtime_makes_no_network_request(monkeypatch):
    monkeypatch.setattr(wechat, 'probe_runtime', lambda: False)
    def forbidden(*args, **kwargs):
        pytest.fail('Search should not fetch a page before its parser is ready')
    monkeypatch.setattr(wechat.requests, 'get', forbidden)
    with pytest.raises(wechat.WeChatSearchError, match='setup-wechat'):
        wechat.search_wechat('test')


class Response:
    def __init__(self, status=200, body=CARDS.encode(), content_type='text/html; charset=utf-8'):
        self.status_code = status
        self.body = body
        self.headers = {'Content-Type': content_type}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, size):
        yield self.body


@pytest.mark.parametrize('status', [302, 403, 429, 500])
def test_http_errors_never_follow_redirects_or_retry(monkeypatch, status):
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return Response(status)
    monkeypatch.setattr(wechat.requests, 'get', get)
    with pytest.raises(wechat.WeChatSearchError, match=f'HTTP {status}'):
        wechat._fetch_listing('机械之心', 2)
    assert len(calls) == 1
    assert calls[0][0] == 'https://weixin.sogou.com/weixin'
    assert calls[0][1]['allow_redirects'] is False
    assert calls[0][1]['params']['page'] == '2'


@pytest.mark.parametrize('response', [
    Response(content_type='application/json'),
    Response(body=b'x' * (wechat.MAX_HTML_BYTES + 1)),
])
def test_non_html_and_oversize_fail(monkeypatch, response):
    monkeypatch.setattr(wechat.requests, 'get', lambda *a, **kw: response)
    with pytest.raises(wechat.WeChatSearchError):
        wechat._fetch_listing('test', 1)


def test_network_timeout_is_an_error(monkeypatch):
    def timeout(*args, **kwargs):
        raise requests.Timeout()
    monkeypatch.setattr(wechat.requests, 'get', timeout)
    with pytest.raises(wechat.WeChatSearchError, match='超时'):
        wechat._fetch_listing('test', 1)


@pytest.mark.parametrize('query,limit,page', [('', 10, 1), ('x', 0, 1), ('x', 11, 1), ('x', 10, 0)])
def test_invalid_input_is_rejected(query, limit, page):
    with pytest.raises(wechat.WeChatSearchError):
        wechat.search_wechat(query, limit, page)


def test_cli_error_has_nonzero_exit_and_no_false_total(monkeypatch, capsys):
    def fail(*args):
        raise wechat.WeChatSearchError('blocked')
    monkeypatch.setattr(wechat, 'search_wechat', fail)
    monkeypatch.setattr(sys, 'argv', ['agent-reach', 'search-wechat', 'test', '--json'])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert json.loads(capsys.readouterr().out) == {'status': 'error', 'error': 'blocked'}


def test_doctor_does_not_claim_remote_or_article_readiness(monkeypatch):
    monkeypatch.setattr('agent_reach.channels.wechat.probe_runtime', lambda: True)
    channel = WeChatChannel()
    assert channel.check()[0] == 'warn'
    assert channel.active_backend is None
    assert not channel.can_handle('https://mp.weixin.qq.com/s/example')


def test_setup_uses_lockfile_without_lifecycle_scripts(monkeypatch, tmp_path):
    monkeypatch.setattr(wechat, 'runtime_dir', lambda: tmp_path / 'runtime')
    monkeypatch.setattr(wechat.shutil, 'which', lambda name: '/bin/' + name)
    monkeypatch.setattr(wechat, 'probe_runtime', lambda: True)
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, '', '')
    monkeypatch.setattr(wechat.subprocess, 'run', run)
    wechat.setup_runtime()
    assert len(calls) == 1
    assert calls[0][0] == ['/bin/npm', 'ci', '--ignore-scripts', '--no-audit', '--no-fund']
    assert (tmp_path / 'runtime' / 'package-lock.json').read_bytes() == (
        wechat.ASSETS / 'package-lock.json'
    ).read_bytes()
