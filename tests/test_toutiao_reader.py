"""Single-article extraction must fail closed on gates, false bodies and identity changes."""

import json
from unittest.mock import MagicMock

import pytest
import requests

from agent_reach.readers import toutiao
from agent_reach.readers.toutiao import ToutiaoReadError, normalize_article_url, read_article

ARTICLE_ID = "7590303258061767194"
URL = f"https://www.toutiao.com/article/{ARTICLE_ID}/"
MOBILE_URL = f"https://m.toutiao.com/i{ARTICLE_ID}/info/"
IMAGE = "https://p3-sign.toutiaoimg.com/tos-cn-i-example/pic.jpeg?x-signature=a%2Bb"


def mobile(**fields):
    data = {
        "gid": ARTICLE_ID, "title": "新年的城市散步", "source": "城市观察",
        "publish_time": "1767255199", "content": "<p>沿河步行，记录这座城市的日常。</p>",
        # Syndicated articles can expose an external original publisher URL.
        "url": "https://publisher.example.com/news/42",
    }
    data.update(fields)
    return json.dumps({"success": True, "data": data}, ensure_ascii=False)


def jina(body="沿河步行，记录这座城市的日常。", *, url=URL, title="新年的城市散步", nav=False):
    navigation = "* 关注\n* 推荐\n搜索\n登录\n\n" if nav else ""
    footer = (
        f"\n\n举报\n\n评论 10\n\n请先 登录 后发表评论～\n![头像]({IMAGE})"
        "\n\n## 头条热榜\n推荐的另一篇文章\n登录后内容更精彩"
        if nav else ""
    )
    return (
        f"Title: {title}\n\nURL Source: {url}\n\nPublished Time: 2026-01-01T16:13:19+08:00"
        "\n\nMarkdown Content:\n"
        f"{navigation}# {title}\n\n2026-01-01 16:13·[城市观察](https://www.toutiao.com/c/user/123/)"
        f"\n\n{body}{footer}"
    )


def serve(monkeypatch, *responses):
    fetch = MagicMock(side_effect=responses)
    monkeypatch.setattr(toutiao, "_fetch", fetch)
    return fetch


@pytest.mark.parametrize("url", [
    URL, f"http://toutiao.com/article/{ARTICLE_ID}",
    f"https://m.toutiao.com/i{ARTICLE_ID}/?source=share#ignored",
    f"www.toutiao.com/article/{ARTICLE_ID}/?tracking=discard",
])
def test_normalizes_supported_links_without_tracking(url):
    assert normalize_article_url(url) == URL


@pytest.mark.parametrize("url", [
    "https://www.toutiao.com.evil.test/article/7590303258061767194/",
    "https://www.toutiao.com@evil.test/article/7590303258061767194/",
    "https://user:password@www.toutiao.com/article/7590303258061767194/",
    "https://localhost/article/7590303258061767194/",
    "https://www.toutiao.com:9000/article/7590303258061767194/",
    "https://so.toutiao.com/search?keyword=文章",
    "https://www.toutiao.com/c/user/123/",
    "https://www.toutiao.com/video/7590303258061767194/",
    "https://www.toutiao.com/article/7590303258061767194/extra",
    "https://www.toutiao.com/article/%37%35%39/",
    "javascript:alert(1)", "", "https://www.toutiao.com\\@evil.test/article/7590303258061767194/",
])
def test_rejects_unsupported_or_disguised_urls_before_network(monkeypatch, url):
    fetch = serve(monkeypatch)
    with pytest.raises(ToutiaoReadError) as error:
        read_article(url)
    assert error.value.code == "invalid_url"
    fetch.assert_not_called()


def test_mobile_extracts_body_and_images_without_page_code(monkeypatch):
    html = (
        '<script>secret()</script><nav>推荐导航</nav><p>开头 &amp; 背景。</p>'
        f'<img src="{IMAGE}" alt="河边风景" onerror="evil()">'
        '<p>第二段<br>结尾。</p><iframe src="https://evil.test">嵌入垃圾</iframe>'
    )
    fetch = serve(monkeypatch, mobile(content=html))
    article = read_article(URL + "?share=1")
    assert article["article_id"] == ARTICLE_ID
    assert article["url"] == URL
    assert article["source"] == "城市观察"
    assert article["published_at"] == "2026-01-01T16:13:19+08:00"
    assert article["backend"] == "mobile_api"
    assert article["images"] == [{"url": IMAGE, "alt": "河边风景"}]
    assert "开头 & 背景。" in article["text"] and "结尾。" in article["text"]
    assert all(word not in article["content"] for word in ("secret", "onerror", "推荐导航", "嵌入垃圾"))
    assert article["retrieved_at"].endswith("+00:00")
    fetch.assert_called_once_with(MOBILE_URL, "mobile_api")


def test_pure_image_article_is_valid_and_does_not_claim_ocr(monkeypatch):
    serve(monkeypatch, mobile(content=f'<img data-src="{IMAGE}" alt="图表"><img src="{IMAGE}">'))
    article = read_article(URL)
    assert article["text"] == ""
    assert len(article["images"]) == 1
    assert any("未识别图片内文字" in warning for warning in article["warnings"])


def test_private_and_active_image_sources_are_not_returned(monkeypatch):
    html = '<p>正文。</p><img src="http://127.0.0.1/a"><img src="javascript:evil()"><img src="data:image/png,x">'
    serve(monkeypatch, mobile(content=html))
    assert read_article(URL)["images"] == []


def test_loading_placeholder_is_not_article_content(monkeypatch):
    serve(monkeypatch, mobile(content="<p>视频加载中...</p><p>视频配文说明。</p>"))
    article = read_article(URL)
    assert article["text"] == article["content"] == "视频配文说明。"
    assert any("未读取或转写媒体内容" in warning for warning in article["warnings"])


def test_loading_placeholder_alone_is_not_a_success(monkeypatch):
    serve(monkeypatch, mobile(content="<p>视频加载中...</p>"), jina("视频加载中..."))
    with pytest.raises(ToutiaoReadError) as error:
        read_article(URL)
    assert error.value.code == "empty_content"


def test_real_html_table_preserves_rows_and_cell_boundaries(monkeypatch):
    table = (
        "<p>年度对比：</p><table><thead><tr><th>项目</th><th>2025</th><th>2026</th></tr></thead>"
        "<tbody><tr><td>数量</td><td>100</td><td>200</td></tr>"
        "<tr><td>单位</td><td>件</td><td>件</td></tr></tbody></table>"
    )
    serve(monkeypatch, mobile(content=table))
    article = read_article(URL)
    for field in ("content", "text"):
        assert "项目 | 2025 | 2026" in article[field]
        assert "数量 | 100 | 200" in article[field]
        assert "单位 | 件 | 件" in article[field]
        assert "100200" not in article[field]


@pytest.mark.parametrize("tag", ["video", "audio", "iframe", "object", "embed"])
def test_embedded_media_is_reported_as_not_transcribed(monkeypatch, tag):
    embed = f'<{tag} src="https://example.com/media">备用播放提示</{tag}>'
    if tag == "embed":
        embed = '<embed src="https://example.com/media">'
    serve(monkeypatch, mobile(content=f"<p>作品文字简介。</p>{embed}"))
    article = read_article(URL)
    assert article["text"] == "作品文字简介。"
    assert any("未读取或转写媒体内容" in warning for warning in article["warnings"])
    assert "_unread_media" not in article


def test_jina_video_caption_keeps_media_limitation(monkeypatch):
    serve(monkeypatch, "not JSON", jina("视频加载中...\n\n这是一段视频的文字简介。"))
    article = read_article(URL)
    assert article["text"] == "这是一段视频的文字简介。"
    assert any("未读取或转写媒体内容" in warning for warning in article["warnings"])


@pytest.mark.parametrize(("value", "expected"), [
    (1767255199, "2026-01-01T16:13:19+08:00"),
    ("1767255199000", "2026-01-01T16:13:19+08:00"),
    ("2026-01-01T08:13:19Z", "2026-01-01T08:13:19+00:00"),
    ("2026/1/1 16:13:19", "2026-01-01T16:13:19+08:00"),
    ("2026年1月1日 16:13:19", "2026-01-01T16:13:19+08:00"),
    ("2026-01-01", "2026-01-01"),
    ("2001-07-01 08:00:00", "2001-07-01T08:00:00+08:00"),
    ("2001-01-01 08:00:00", "2001-01-01T08:00:00+08:00"),
    ("昨天", None), (True, None), ("9999999999999", None),
])
def test_date_formats_do_not_invent_missing_dates(monkeypatch, value, expected):
    serve(monkeypatch, mobile(publish_time=value))
    article = read_article(URL)
    assert article["published_at"] == expected
    if expected is None:
        assert any("发布时间" in warning for warning in article["warnings"])


def test_jina_fallback_cuts_comments_and_avatars(monkeypatch):
    fetch = serve(monkeypatch, '<html><script>_$jsvmprt</script></html>', jina(nav=True))
    article = read_article(URL)
    assert article["backend"] == "jina"
    assert article["text"] == "沿河步行，记录这座城市的日常。"
    assert article["images"] == []
    assert "头条热榜" not in article["content"]
    assert "评论" not in article["content"]
    assert any("缓存" in warning for warning in article["warnings"])
    assert fetch.call_count == 2


def test_jina_pure_image_article(monkeypatch):
    serve(monkeypatch, "not JSON", jina(f"![风景]({IMAGE})"))
    article = read_article(URL)
    assert article["images"] == [{"url": IMAGE, "alt": "风景"}]
    assert article["text"] == ""


@pytest.mark.parametrize("raw", [
    '<html><title>安全验证</title>请完成下方验证后继续访问</html>',
    '{"success":false,"message":"captcha required"}',
    '{"data":{"code":403,"error":"forbidden"}}',
    mobile(is_private=True), mobile(is_deleted=True), mobile(title="内容已删除"),
])
def test_access_gates_never_trigger_another_backend(monkeypatch, raw):
    fetch = serve(monkeypatch, raw, jina())
    with pytest.raises(ToutiaoReadError) as error:
        read_article(URL)
    assert error.value.code == "access_denied"
    assert fetch.call_count == 1


def test_an_article_discussing_captcha_is_not_itself_a_challenge(monkeypatch):
    serve(monkeypatch, mobile(title="验证码为什么难辨认", content="<p>本文讨论验证码的可用性和设计。</p>"))
    assert read_article(URL)["title"] == "验证码为什么难辨认"


@pytest.mark.parametrize("code", ["access_denied", "rate_limited", "unsafe_redirect", "response_too_large"])
def test_terminal_transport_failure_does_not_fallback(monkeypatch, code):
    fetch = serve(monkeypatch, ToutiaoReadError(code, "已停止"), jina())
    with pytest.raises(ToutiaoReadError) as error:
        read_article(URL)
    assert error.value.code == code
    assert fetch.call_count == 1


def test_mobile_article_id_mismatch_is_terminal(monkeypatch):
    fetch = serve(monkeypatch, mobile(gid="7681879207089914419"), jina())
    with pytest.raises(ToutiaoReadError, match="编号") as error:
        read_article(URL)
    assert error.value.code == "identity_mismatch"
    assert fetch.call_count == 1


@pytest.mark.parametrize("fallback", [
    jina(url="https://www.toutiao.com/article/7681879207089914419/"),
    jina(url="https://publisher.example.com/article/7590303258061767194/"),
    jina().replace("# 新年的城市散步", "# 另一篇文章"),
])
def test_jina_cannot_substitute_a_different_article(monkeypatch, fallback):
    serve(monkeypatch, "not JSON", fallback)
    with pytest.raises(ToutiaoReadError) as error:
        read_article(URL)
    assert error.value.code == "identity_mismatch"


@pytest.mark.parametrize("raw", [
    jina("打开APP阅读全文"),
    "Warning: Content truncated\n" + jina(),
    jina("视频加载中..."),
    jina().replace("2026-01-01 16:13·", "无文章元数据 "),
    jina("<script>bad()</script>正文。"),
    jina("请先 登录 后发表评论～"),
])
def test_jina_rejects_missing_or_unfinished_bodies(monkeypatch, raw):
    serve(monkeypatch, "not JSON", raw)
    with pytest.raises(ToutiaoReadError):
        read_article(URL)


def test_explicit_partial_mobile_article_is_not_a_success(monkeypatch):
    fetch = serve(monkeypatch, mobile(content_truncated=True), jina())
    with pytest.raises(ToutiaoReadError) as error:
        read_article(URL)
    assert error.value.code == "incomplete_content"
    assert fetch.call_count == 1


class Response:
    def __init__(self, body=b"", status=200, headers=None):
        self.body = body
        self.status_code = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_content(self, chunk_size):
        yield self.body


def transport(monkeypatch, *responses):
    session = MagicMock()
    session.__enter__.return_value = session
    session.get.side_effect = responses
    monkeypatch.setattr(toutiao.requests, "Session", lambda: session)
    return session


@pytest.mark.parametrize("status", [401, 403, 407, 429, 451])
def test_http_access_restrictions_are_terminal(monkeypatch, status):
    session = transport(monkeypatch, Response(status=status))
    with pytest.raises(ToutiaoReadError) as error:
        read_article(URL)
    assert error.value.code in {"rate_limited", "access_denied"}
    assert session.get.call_count == 1


@pytest.mark.parametrize("target", [
    "http://127.0.0.1/private", "https://m.toutiao.com.evil.test/article/7590303258061767194/",
    "https://m.toutiao.com/i7681879207089914419/info/",
    "https://www.toutiao.com/login/", "http://m.toutiao.com/i7590303258061767194/info/",
])
def test_redirect_is_checked_before_following(monkeypatch, target):
    session = transport(monkeypatch, Response(status=302, headers={"Location": target}))
    with pytest.raises(ToutiaoReadError) as error:
        read_article(URL)
    assert error.value.code in {"unsafe_redirect", "access_denied"}
    assert session.get.call_count == 1


def test_stream_is_not_truncated_into_a_success(monkeypatch):
    monkeypatch.setattr(toutiao, "MAX_RESPONSE_BYTES", 12)
    session = transport(monkeypatch, Response(b"x" * 13))
    with pytest.raises(ToutiaoReadError) as error:
        read_article(URL)
    assert error.value.code == "response_too_large"
    assert session.get.call_count == 1


def test_transport_uses_bounded_public_requests(monkeypatch):
    session = transport(monkeypatch, Response(mobile().encode()))
    article = read_article(URL)
    assert article["backend"] == "mobile_api"
    assert session.trust_env is False
    assert session.get.call_args.kwargs["allow_redirects"] is False
    assert session.get.call_args.kwargs["stream"] is True
    assert session.get.call_args.kwargs["timeout"] == (5, 10)
    assert "Cookie" not in session.get.call_args.kwargs["headers"]


def test_network_timeout_can_use_jina(monkeypatch):
    session = transport(monkeypatch, requests.Timeout(), Response(jina().encode()))
    assert read_article(URL)["backend"] == "jina"
    assert session.get.call_count == 2
