"""User-facing Toutiao command, registry and error reporting contracts."""

import json

import pytest

from agent_reach.channels import get_channel
from agent_reach.cli import main


def _article():
    return {
        "article_id": "7590303258061767194",
        "url": "https://www.toutiao.com/article/7590303258061767194/",
        "title": "测试文章",
        "source": "测试报",
        "published_at": None,
        "content": "第一段。\n\n第二段。",
        "text": "第一段。\n\n第二段。",
        "images": [],
        "retrieved_at": "2026-09-09T00:00:00+00:00",
        "backend": "mobile_api",
        "warnings": [],
    }


def test_cli_json_keeps_missing_metadata_and_body(monkeypatch, capsys):
    monkeypatch.setattr("agent_reach.readers.toutiao.read_article", lambda url: _article())
    monkeypatch.setattr("sys.argv", ["agent-reach", "read-toutiao", _article()["url"], "--json"])
    main()
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["ok"] is True
    assert result["published_at"] is None
    assert result["content"] == "第一段。\n\n第二段。"
    assert captured.err == ""


def test_cli_markdown_includes_provenance_and_warnings(monkeypatch, capsys):
    article = _article()
    article["warnings"] = ["部分媒体未识别"]
    monkeypatch.setattr("agent_reach.readers.toutiao.read_article", lambda url: article)
    monkeypatch.setattr("sys.argv", ["agent-reach", "read-toutiao", article["url"]])
    main()
    captured = capsys.readouterr()
    assert "# 测试文章" in captured.out
    assert article["url"] in captured.out
    assert article["content"] in captured.out
    assert "部分媒体未识别" in captured.out
    assert "发布时间：" not in captured.out
    assert captured.err == ""


@pytest.mark.parametrize("as_json", [False, True])
def test_cli_errors_are_explicit_and_nonzero(monkeypatch, capsys, as_json):
    from agent_reach.readers.toutiao import ToutiaoReadError

    def fail(url):
        raise ToutiaoReadError("verification_required", "页面需要验证，未取得正文")

    monkeypatch.setattr("agent_reach.readers.toutiao.read_article", fail)
    monkeypatch.setattr("sys.argv", ["agent-reach", "read-toutiao", _article()["url"]]
                        + (["--json"] if as_json else []))
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    captured = capsys.readouterr()
    if as_json:
        result = json.loads(captured.out)
        assert result["ok"] is False
        assert result["error"]["code"] == "verification_required"
        assert captured.err == ""
    else:
        assert captured.out == ""
        assert "页面需要验证" in captured.err


def test_registered_channel_only_accepts_article_urls():
    channel = get_channel("toutiao")
    assert channel is not None
    assert channel.can_handle(_article()["url"])
    assert not channel.can_handle("https://www.toutiao.com/")
    assert not channel.can_handle("https://toutiao.com.evil.test/article/7590303258061767194/")
    assert not channel.can_handle("https://www.toutiao.com/video/7590303258061767194/")


def test_channel_check_does_not_claim_live_success(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: pytest.fail("unexpected request"))
    channel = get_channel("toutiao")
    status, message = channel.check()
    assert status == "warn"
    assert "未联网验证" in message
    assert channel.active_backend is None
