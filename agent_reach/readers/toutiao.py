"""Read one public Toutiao article; never search, authenticate, or fetch media.

The mobile endpoint is an undocumented first-party interface. Jina is a
best-effort fallback for transport/format failures, not access restrictions.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests

from agent_reach.utils.url import normalize_public_http_url

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_FETCH_SECONDS = 20
_CHINA_TZ = timezone(timedelta(hours=8))
_HOSTS = {"toutiao.com", "www.toutiao.com", "m.toutiao.com"}
_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(<?(https?://[^\s>]+?)>?\)")
_RETRYABLE = {"network_error", "upstream_unavailable", "invalid_response", "empty_content"}
_STOP_TITLES = {
    "验证码", "安全验证", "访问验证", "访问受限", "访问异常", "请输入验证码",
    "内容已删除", "文章已删除", "页面不存在", "内容不存在", "请先登录",
    "just a moment...", "access denied", "attention required! | cloudflare",
}


class ToutiaoReadError(RuntimeError):
    """A machine-readable error whose message is suitable for CLI display."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def normalize_article_url(url: str) -> str:
    """Accept explicit article-ID links only and discard tracking parameters."""
    try:
        normalized = normalize_public_http_url(url)
        parsed = urlsplit(normalized)
        if parsed.hostname not in _HOSTS or parsed.port not in (None, 80, 443):
            raise ValueError
        match = re.fullmatch(r"/(?:article/(\d{10,25})|i(\d{10,25}))/?", parsed.path)
        if not match:
            raise ValueError
    except (TypeError, ValueError):
        raise ToutiaoReadError(
            "invalid_url", "请提供今日头条公开文章链接（/article/文章编号/ 或 /i文章编号/）。"
        ) from None
    article_id = match.group(1) or match.group(2)
    return f"https://www.toutiao.com/article/{article_id}/"


def _article_id(url: str) -> str:
    return normalize_article_url(url).split("/")[-2]


def _safe_image(url: str) -> str | None:
    if url.startswith("//"):
        url = "https:" + url
    try:
        return normalize_public_http_url(url) if url.startswith(("http://", "https://")) else None
    except ValueError:
        return None


def _compact(text: str) -> str:
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _remove_loading_placeholder(text: str) -> str:
    return re.sub(r"(?m)^视频加载中(?:\.{3}|…+)\s*\n?", "", text).strip()


class _ArticleHTML(HTMLParser):
    """Extract article text and image references without retaining active HTML."""

    _DROP = {"script", "style", "iframe", "object", "video", "audio", "svg", "noscript", "form", "nav", "footer"}
    _MEDIA = {"iframe", "object", "video", "audio", "embed"}
    _BLOCK = {"p", "div", "section", "article", "h1", "h2", "h3", "h4", "li", "blockquote", "figcaption", "tr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.markdown: list[str] = []
        self.plain: list[str] = []
        self.images: list[dict[str, str]] = []
        self.has_media = False
        self._table_cells = 0
        self._dropped: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._MEDIA:
            self.has_media = True
        if tag in self._DROP:
            self._dropped.append(tag)
        if self._dropped:
            return
        if tag == "tr":
            self._table_cells = 0
        if tag in {"th", "td"}:
            if self._table_cells:
                self.markdown.append(" | ")
                self.plain.append(" | ")
            self._table_cells += 1
        if tag in self._BLOCK or tag == "br":
            self.markdown.append("\n\n")
            self.plain.append("\n\n")
        if tag == "img":
            attributes = dict(attrs)
            src = _safe_image(attributes.get("data-src") or attributes.get("src") or "")
            if src:
                alt = attributes.get("alt") or ""
                if not any(image["url"] == src for image in self.images):
                    self.images.append({"url": src, "alt": alt})
                escaped_alt = alt.replace("[", "\\[").replace("]", "\\]").replace("\n", " ")
                self.markdown.append(f"\n\n![{escaped_alt}](<{src}>)\n\n")

    def handle_endtag(self, tag: str) -> None:
        if self._dropped:
            if tag == self._dropped[-1]:
                self._dropped.pop()
            return
        if tag in self._BLOCK:
            self.markdown.append("\n\n")
            self.plain.append("\n\n")

    def handle_data(self, data: str) -> None:
        if not self._dropped:
            self.plain.append(data)
            # Text from HTML must not become executable HTML or Markdown images.
            escaped = re.sub(r"([\\\[\]<>])", r"\\\1", data)
            self.markdown.append(escaped)


def _html_content(raw: str) -> tuple[str, str, list[dict[str, str]], bool]:
    parser = _ArticleHTML()
    parser.feed(raw)
    parser.close()
    return (
        _remove_loading_placeholder(_compact("".join(parser.markdown))),
        _remove_loading_placeholder(_compact("".join(parser.plain))),
        parser.images,
        parser.has_media or bool(re.search(r"视频加载中(?:\.{3}|…+)", "".join(parser.plain))),
    )


def _published_at(value: Any) -> str | None:
    """Return ISO 8601, interpreting undated-zone Chinese page times as Shanghai."""
    try:
        if isinstance(value, bool) or value is None:
            return None
        candidate = str(value).strip()
        if re.fullmatch(r"\d{10}(?:\d{3})?", candidate):
            number = int(candidate)
            if len(candidate) == 13:
                number //= 1000
            date = datetime.fromtimestamp(number, timezone.utc).astimezone(_CHINA_TZ)
        else:
            candidate = candidate.replace("年", "-").replace("月", "-").replace("日", "").replace("/", "-")
            candidate = re.sub(
                r"^(\d{4})-(\d{1,2})-(\d{1,2})",
                lambda match: f"{match[1]}-{int(match[2]):02d}-{int(match[3]):02d}", candidate,
            )
            date = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            if date.tzinfo is None:
                date = date.replace(tzinfo=_CHINA_TZ)
        if date.year < 2000 or date.year > datetime.now(timezone.utc).year + 1:
            return None
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate):
            return date.date().isoformat()
        return date.isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def _reject_page(body: str) -> None:
    """Recognize access/error pages, without treating quoted article words as gates."""
    first = body[:8192]
    title = re.search(r"(?:^Title:\s*|<title[^>]*>)([^\n<]*)", first, re.I | re.M)
    if title and title.group(1).strip().casefold() in _STOP_TITLES:
        raise ToutiaoReadError("access_denied", "上游返回验证、登录或不可访问页面，未获取文章。")
    if re.search(
        r"^Warning:.*(?:captcha|access denied|403|401|451|429|rate.limit|blocked|forbidden)",
        first, re.I | re.M,
    ) or any(marker in first for marker in (
        "当前环境异常，完成验证后即可继续访问", "请完成下方验证后继续访问", "secsdk-captcha",
        'id="captcha_container"', "/cdn-cgi/challenge-platform/",
    )):
        raise ToutiaoReadError("access_denied", "上游要求访问验证或拒绝访问，已停止，未尝试换路绕过。")
    if re.search(r"^Warning:.*(?:truncat|incomplete|partial)", first, re.I | re.M):
        raise ToutiaoReadError("incomplete_content", "上游提示正文被截断，未返回成功结果。")


def _validate_content(title: str, text: str, images: list[dict[str, str]]) -> None:
    if title.casefold() in _STOP_TITLES:
        raise ToutiaoReadError("access_denied", "上游返回验证或不可访问页面，已停止。")
    if not title.strip():
        raise ToutiaoReadError("invalid_response", "没有识别到有效文章标题。")
    if not text.strip() and not images:
        raise ToutiaoReadError("empty_content", "没有获取到文章正文或图片引用。")
    if re.search(r"(?:展开全文|展开剩余全文|打开APP阅读全文|打开今日头条.*?全文|点击查看全文)\s*$", text, re.I):
        raise ToutiaoReadError("incomplete_content", "页面只提供部分内容或要求展开全文。")
    if text.strip().casefold() in _STOP_TITLES:
        raise ToutiaoReadError("access_denied", "正文位置返回了验证或不可访问提示。")


def _validate_redirect(target: str, original: str, backend: str) -> None:
    try:
        safe = normalize_public_http_url(target)
        parsed = urlsplit(safe)
        if parsed.scheme != "https" or parsed.port not in (None, 443):
            raise ValueError
        if re.search(r"login|captcha|verify|passport|challenge", parsed.path, re.I):
            raise ToutiaoReadError("access_denied", "上游跳转到登录或验证页面，已停止。")
        if backend == "jina":
            if parsed.hostname != "r.jina.ai" or target != original:
                raise ValueError
        else:
            expected_id = re.search(r"/i(\d+)/info/", original)
            if parsed.hostname not in _HOSTS or not expected_id:
                raise ValueError
            paths = {f"/i{expected_id.group(1)}/info/", f"/article/{expected_id.group(1)}/"}
            if parsed.path not in paths or parsed.query or parsed.fragment:
                raise ValueError
    except ValueError:
        raise ToutiaoReadError("unsafe_redirect", "上游跳转离开允许的文章地址，已停止。") from None


def _fetch(url: str, backend: str) -> str:
    """Bound redirects, decoded response size and network waits; send no credentials."""
    started = time.monotonic()
    current = url
    try:
        with requests.Session() as session:
            # Do not silently use ~/.netrc credentials for these public requests.
            session.trust_env = False
            for _ in range(4):
                remaining = _MAX_FETCH_SECONDS - (time.monotonic() - started)
                if remaining <= 0:
                    raise ToutiaoReadError("network_error", "文章读取超时。")
                _validate_redirect(current, url, backend)
                session.cookies.clear()
                with session.get(
                    current, headers={"User-Agent": _UA, "Accept": "text/plain" if backend == "jina" else "application/json"},
                    timeout=(min(5, remaining), min(10, remaining)), allow_redirects=False, stream=True,
                ) as response:
                    status = response.status_code
                    if status in {401, 403, 407, 429, 451}:
                        code = "rate_limited" if status == 429 else "access_denied"
                        raise ToutiaoReadError(code, f"上游拒绝访问或限制请求（HTTP {status}），已停止。")
                    if status in {301, 302, 303, 307, 308}:
                        location = response.headers.get("Location", "")
                        if not location:
                            raise ToutiaoReadError("invalid_response", "上游重定向缺少目标地址。")
                        current = urljoin(current, location)
                        continue
                    if status != 200:
                        raise ToutiaoReadError("upstream_unavailable", f"上游接口暂不可用（HTTP {status}）。")
                    chunks: list[bytes] = []
                    size = 0
                    for chunk in response.iter_content(chunk_size=16384):
                        size += len(chunk)
                        if size > MAX_RESPONSE_BYTES:
                            raise ToutiaoReadError("response_too_large", "文章响应超过大小上限，未截断后冒充成功。")
                        if time.monotonic() - started > _MAX_FETCH_SECONDS:
                            raise ToutiaoReadError("network_error", "文章读取超时。")
                        chunks.append(chunk)
                    try:
                        return b"".join(chunks).decode("utf-8-sig")
                    except UnicodeDecodeError:
                        raise ToutiaoReadError("invalid_response", "上游返回了无法解码的内容。") from None
        raise ToutiaoReadError("unsafe_redirect", "上游重定向次数超过限制。")
    except requests.RequestException:
        raise ToutiaoReadError("network_error", "文章读取连接失败或超时。") from None


def _read_mobile(raw: str, url: str) -> dict[str, Any]:
    _reject_page(raw)
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        raise ToutiaoReadError("invalid_response", "移动接口未返回文章 JSON。") from None
    if not isinstance(payload, dict):
        raise ToutiaoReadError("invalid_response", "移动接口数据格式异常。")
    data = payload.get("data")
    errors = [payload] + ([data] if isinstance(data, dict) and not data.get("gid") else [])
    error_text = " ".join(str(item.get(k, "")) for item in errors for k in (
        "message", "error", "error_code", "code", "status_code", "status_msg",
    ))
    if re.search(r"forbidden|unauthori|captcha|verify|login|denied|blocked|rate.?limit|验证码|验证|登录|权限|限流|封禁|\b(?:401|403|429|451)\b", error_text, re.I):
        raise ToutiaoReadError("access_denied", "移动接口要求验证或拒绝访问，已停止。")
    if payload.get("success") is False or not isinstance(data, dict):
        raise ToutiaoReadError("invalid_response", "移动接口没有返回有效文章数据。")
    if not data.get("gid"):
        raise ToutiaoReadError("invalid_response", "移动接口缺少可核对的文章编号。")
    if str(data.get("gid", "")) != _article_id(url):
        raise ToutiaoReadError("identity_mismatch", "返回的文章编号与请求不符，已停止。")
    if any(data.get(key) is True for key in ("is_deleted", "deleted", "is_private")):
        raise ToutiaoReadError("access_denied", "文章已删除或不是公开内容。")
    if any(data.get(key) is True for key in ("is_partial", "content_truncated", "is_content_truncated")):
        raise ToutiaoReadError("incomplete_content", "接口标记正文不完整，未返回成功结果。")
    title = data.get("title")
    html = data.get("content")
    if not isinstance(title, str) or not isinstance(html, str):
        raise ToutiaoReadError("invalid_response", "文章标题或正文数据格式异常。")
    content, text, images, has_media = _html_content(html)
    _validate_content(title.strip(), text, images)
    return {"title": title.strip(), "source": data["source"].strip() if isinstance(data.get("source"), str) else None,
            "published_at": _published_at(data.get("publish_time")), "content": content,
            "text": text, "images": images, "_unread_media": has_media}


def _read_jina(raw: str, url: str) -> dict[str, Any]:
    _reject_page(raw)
    header, separator, body = raw.partition("Markdown Content:")
    title_match = re.search(r"^Title:\s*(.+)$", header, re.M)
    source_url = re.search(r"^URL Source:\s*(\S+)", header, re.M)
    if not separator or not title_match or not source_url:
        raise ToutiaoReadError("invalid_response", "Jina 未返回可核对来源的文章。")
    try:
        matches = normalize_article_url(source_url.group(1)) == url
    except ToutiaoReadError:
        matches = False
    if not matches:
        raise ToutiaoReadError("identity_mismatch", "Jina 返回的来源地址与请求文章不符。")
    title = re.sub(r"\s*[-–|]\s*今日头条\s*$", "", title_match.group(1)).strip()
    metadata = re.search(
        r"^(\d{4}[-/]\d{1,2}[-/]\d{1,2}[ T]\d{1,2}:\d{2}(?::\d{2})?)\s*[·•]\s*(.+)$",
        body, re.M,
    )
    if not metadata:
        raise ToutiaoReadError("invalid_response", "无法确认 Jina 正文起点（缺少文章来源和时间行）。")
    headings = re.findall(r"^#\s+(.+)$", body[:metadata.start()], re.M)
    if headings and headings[-1].strip() != title:
        raise ToutiaoReadError("identity_mismatch", "Jina 页面标题与文章标题不符。")
    source = re.sub(r"\[([^\]]+)\]\([^\n]+\)", r"\1", metadata.group(2)).strip()
    published = re.search(r"^Published Time:\s*(.+)$", header, re.M)
    content = body[metadata.end():].strip()
    # Stop only at recognisable article UI boundaries, never an arbitrary length.
    boundary = re.search(r"(?m)^举报\s*\n+\s*评论(?:\s|\d)|^评论\s*\d+\s*$|^## (?:头条热榜|相关推荐|推荐阅读)\s*$|^扫码下载今日头条APP\s*$", content)
    if boundary:
        content = content[:boundary.start()].strip()
    if re.search(r"(?m)^Warning:|请先\s*登录\s*后发表评论|^登录后内容更精彩$", content):
        raise ToutiaoReadError("invalid_response", "Jina 正文仍混有异常提示或页面区域，无法确认正文边界。")
    has_media = bool(re.search(r"视频加载中(?:\.{3}|…+)", content))
    content = _remove_loading_placeholder(content)
    images: list[dict[str, str]] = []

    def image_replace(match: re.Match[str]) -> str:
        image_url = _safe_image(match.group(2))
        if not image_url:
            return ""
        if not any(item["url"] == image_url for item in images):
            images.append({"url": image_url, "alt": match.group(1)})
        return f"![{match.group(1)}](<{image_url}>)"

    content = _IMAGE_RE.sub(image_replace, content)
    # Remove any inline HTML from the Markdown fallback, retaining its text.
    if re.search(r"</?[A-Za-z][A-Za-z0-9-]*(?:\s|>)", content):
        raise ToutiaoReadError("invalid_response", "Jina 正文仍包含未清洗的 HTML，已停止。")
    text = _IMAGE_RE.sub("", content)
    text = re.sub(r"\[([^\]]+)\]\([^\n]+?\)", r"\1", text)
    text = re.sub(r"(?m)^#{1,6}\s+", "", text)
    text = _compact(text)
    _validate_content(title, text, images)
    return {"title": title, "source": source or None,
            "published_at": _published_at(published.group(1) if published else metadata.group(1)),
            "content": _compact(content), "text": text, "images": images,
            "_unread_media": has_media}


def read_article(url: str) -> dict[str, Any]:
    """Return verified article fields, or raise ``ToutiaoReadError``.

    No successful response asserts that the origin is live or that completeness
    was compared with the author's manuscript. ``warnings`` preserves these limits.
    """
    canonical = normalize_article_url(url)
    article_id = _article_id(canonical)
    warnings: list[str] = []
    backend = "mobile_api"
    try:
        article = _read_mobile(_fetch(f"https://m.toutiao.com/i{article_id}/info/", backend), canonical)
    except ToutiaoReadError as error:
        if error.code not in _RETRYABLE:
            raise
        warnings.append(f"移动接口未成功（{error.code}），本次使用 Jina 后备。")
        backend = "jina"
        article = _read_jina(_fetch(f"https://r.jina.ai/{canonical}", backend), canonical)
        warnings.append("Jina 可能返回缓存内容；未确认其原站抓取时间。")
    if not article["source"]:
        warnings.append("未获取到来源名称。")
    if article["published_at"] is None:
        warnings.append("未获取到可解析的发布时间。")
    if not article["text"]:
        warnings.append("这是一篇纯图片内容，已保留图片引用，未识别图片内文字。")
    if article.pop("_unread_media", False):
        warnings.append("页面含视频或其他嵌入媒体；本次仅读取文字附文和图片引用，未读取或转写媒体内容。")
    if article["images"]:
        warnings.append("图片仅保留地址引用；带签名的地址可能过期，未下载图片。")
    warnings.append("已核对文章编号及正文结构；未与作者原稿逐字比对，接口或缓存也可能变化。")
    return {"article_id": article_id, "url": canonical, **article,
            "retrieved_at": datetime.now(timezone.utc).isoformat(), "backend": backend,
            "warnings": warnings}
