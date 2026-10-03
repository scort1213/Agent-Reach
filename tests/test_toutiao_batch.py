"""Batch acceptance contracts: identity, time, failures and safe resumption."""
from copy import deepcopy
from urllib.parse import quote

import pytest

from agent_reach.readers import toutiao_batch as batch
from agent_reach.readers.toutiao import ToutiaoReadError

URL = "https://www.toutiao.com/article/7683705619378946583/"


def manifest(mode="keyword"):
    return {"mode": mode, "query": "人工智能", "captured_at": "2026-09-10T15:00:00+08:00",
            "list_url": "https://so.toutiao.com/", "discovery_complete": False,
            "items": [{"url": URL, "title": "测试"}]}


def article():
    return {"article_id": "7683705619378946583", "url": URL, "title": "测试", "source": "来源",
            "published_at": "2026-09-10T09:00:00+08:00", "text": "真实正文",
            "content": "真实正文", "images": [], "backend": "mobile_api", "warnings": [],
            "retrieved_at": "2026-09-10T15:00:00+08:00"}


def test_dedup_and_resume_without_duplicate_fetch(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(batch, "read_article", lambda url: (calls.append(url), article())[1])
    data = manifest()
    data["items"] *= 2
    result = batch.read_batch(data, tmp_path, interval=0)
    assert result["successful_bodies"] == 1
    assert result["discovery_complete"] is False
    assert batch.read_batch(data, tmp_path, interval=0)["ok"]
    assert calls == [URL]
    data["query"] = "其他"
    with pytest.raises(ValueError, match="其他列表"):
        batch.read_batch(data, tmp_path)


@pytest.mark.parametrize("date,status", [(None, "date_unknown"),
    ("2026-09-08T09:00:00+08:00", "outside_window"),
    ("2026-09-11T09:00:00+08:00", "outside_window")])
def test_recent_window_is_checked_against_body(monkeypatch, tmp_path, date, status):
    value = article()
    value["published_at"] = date
    monkeypatch.setattr(batch, "read_article", lambda url: value)
    result = batch.read_batch(manifest(), tmp_path)
    assert result["items"][0]["status"] == status
    assert result["successful_bodies"] == 0


def test_account_has_no_default_date_filter(monkeypatch, tmp_path):
    value = article()
    value["published_at"] = "2018-01-01T09:00:00+08:00"
    monkeypatch.setattr(batch, "read_article", lambda url: value)
    assert batch.read_batch(manifest("account"), tmp_path)["successful_bodies"] == 1


@pytest.mark.parametrize("code,second", [("network_error", "success"),
                                      ("access_denied", "not_attempted")])
def test_failures_are_retained_and_denial_stops_batch(monkeypatch, tmp_path, code, second):
    calls = []
    def read(url):
        calls.append(url)
        if len(calls) == 1:
            raise ToutiaoReadError(code, "测试失败")
        return article()
    monkeypatch.setattr(batch, "read_article", read)
    data = manifest()
    data["items"].append({"url": URL.replace("6583", "6584"), "title": "测试"})
    result = batch.read_batch(data, tmp_path, interval=0)
    assert result["ok"] is False
    assert result["items"][0]["status"] == "failed"
    assert result["items"][1]["status"] == second


def test_wrong_title_cannot_be_success(monkeypatch, tmp_path):
    value = article()
    value["title"] = "另一个标题"
    monkeypatch.setattr(batch, "read_article", lambda url: value)
    result = batch.read_batch(manifest(), tmp_path)
    assert result["items"][0]["error"]["code"] == "title_mismatch"


def test_jump_decodes_only_toutiao_without_fetching():
    original = URL.replace("/article/", "/a")
    jump = "https://so.toutiao.com/search/jump?url=" + quote(original, safe="")
    assert batch.listing_url(jump) == URL
    with pytest.raises(ValueError):
        batch.listing_url("https://so.toutiao.com/search/jump?url=https://evil.test/")


def test_all_urls_validated_before_fetch(monkeypatch, tmp_path):
    data = deepcopy(manifest())
    data["items"].append({"url": "https://example.com/"})
    monkeypatch.setattr(batch, "read_article", lambda url: pytest.fail("unexpected fetch"))
    with pytest.raises(ValueError):
        batch.read_batch(data, tmp_path)


def test_observed_zlink_wrapper_decodes_without_network():
    wrapped = 'https://article.zlink.toutiao.com/J4dQM?h5_url=' + quote('https://toutiao.com/group/7684501878540878375/?source=news', safe='')
    link = 'https://so.toutiao.com/search/jump?url=' + quote(wrapped, safe='')
    assert batch.listing_url(link) == 'https://www.toutiao.com/article/7684501878540878375/'
    malicious = 'https://article.zlink.toutiao.com/J4dQM?h5_url=' + quote('https://example.com/group/7684501878540878375/', safe='')
    with pytest.raises(ValueError):
        batch.listing_url(malicious)
