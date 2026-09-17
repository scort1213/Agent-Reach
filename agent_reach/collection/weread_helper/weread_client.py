"""Local WeRead client. No credentials or remote bodies are logged.

Protocol reference: finlater/weread.koplugin, revision 2943080c (AGPL-3.0).
This Python implementation and local interface were added on 2026-09-09.
"""

from __future__ import annotations

import copy
import html
import json
import os
import re
import secrets
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote, urlencode

import requests
from bs4 import BeautifulSoup

BASE = "https://weread.qq.com"
SKILLS = BASE + "/r/weread-skills"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
ACCOUNT_RE = re.compile(r"MP_WXS_[0-9]+\Z")
REVIEW_RE = re.compile(r"MP_WXS_[0-9]+_[A-Za-z0-9_~\-]+\Z")
ALLOWED = {
    "weread.qq.com": {
        "/r/weread-skills",
        "/api/auth/getLoginUid",
        "/api/auth/getLoginInfo",
        "/api/userInfo",
        "/api/skills/apikeyGet",
        "/web/login/renewal",
        "/web/mp/articles",
        "/web/mp/content",
        "/web/book/info",
    },
    "i.weread.qq.com": {"/api/agent/gateway"},
}


class WeReadError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    temporary = path.with_name(path.name + "." + secrets.token_hex(6) + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def clean_label(value):
    return html.unescape(re.sub(r"<[^>]+>", "", str(value or ""))).strip()


def walk_objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_objects(child)


def parse_accounts(data):
    found = {}
    for item in walk_objects(data):
        identifier = item.get("bookId") or item.get("accountId") or item.get("book_id")
        if not isinstance(identifier, str) or not ACCOUNT_RE.fullmatch(identifier):
            continue
        name = clean_label(
            item.get("title")
            or item.get("name")
            or item.get("mpName")
            or item.get("mp_name")
            or item.get("nickname")
        )
        if not name:
            continue
        found[identifier] = {
            "id": identifier,
            "name": name,
            "intro": clean_label(item.get("intro") or item.get("description"))[:600],
        }
    return list(found.values())


def parse_articles(data, account_id):
    if not isinstance(data, dict) or not isinstance(data.get("reviews"), list):
        raise WeReadError("directory_format", "文章目录返回了未知格式，未把它当作空目录。")
    articles, seen = [], set()
    for group in data["reviews"]:
        if not isinstance(group, dict) or not isinstance(group.get("subReviews", []), list):
            raise WeReadError("directory_format", "文章目录结构发生变化，请更新工具后重试。")
        for sub in group.get("subReviews", []):
            if not isinstance(sub, dict):
                raise WeReadError("directory_format", "文章目录结构发生变化，请更新工具后重试。")
            review = sub.get("review") or sub
            mp = review.get("mpInfo") or {}
            review_id = review.get("reviewId") or sub.get("reviewId")
            if not isinstance(review_id, str) or not REVIEW_RE.fullmatch(review_id):
                continue
            if not review_id.startswith(account_id + "_"):
                continue
            if review_id in seen:
                continue
            seen.add(review_id)
            articles.append(
                {
                    "id": review_id,
                    "title": clean_label(mp.get("title")),
                    "published": review.get("createTime"),
                    "account_id": account_id,
                }
            )
    if data["reviews"] and not articles:
        raise WeReadError("directory_format", "目录中有记录，但没有识别出该账号的有效文章编号。")
    return articles


class BodyBoundary(HTMLParser):
    """Check the source, before BeautifulSoup repairs missing closing tags."""

    def __init__(self):
        super().__init__()
        self.tag = None
        self.depth = 0
        self.closed = False

    def handle_starttag(self, tag, attrs):
        if self.closed:
            return
        if self.tag is None and dict(attrs).get("id") == "js_content":
            self.tag, self.depth = tag, 1
        elif self.tag == tag:
            self.depth += 1

    def handle_endtag(self, tag):
        if self.tag == tag and not self.closed:
            self.depth -= 1
            if self.depth == 0:
                self.closed = True


def extract_article(source: str, expected_title: str):
    soup = BeautifulSoup(source, "html.parser")
    for node in soup(["script", "style", "noscript"]):
        node.decompose()
    body = soup.find(id="js_content")
    if body is None:
        visible = soup.get_text(" ", strip=True)
        if any(x in visible for x in ("访问过于频繁", "环境异常", "安全验证", "请完成验证")):
            raise WeReadError(
                "verification_required", "微信读书要求验证或限制了访问，请在官方客户端处理后再试。"
            )
        if "登录" in visible:
            raise WeReadError("login_expired", "正文请求要求重新登录。")
        raise WeReadError("missing_body", "返回页面没有正文区域，未保存为文章。")
    boundary = BodyBoundary()
    boundary.feed(source)
    if not boundary.closed:
        raise WeReadError("partial_body", "正文区域没有完整结束，可能下载中断，未保存为完整文章。")
    # Paid/preview-only pages must not silently appear as complete exports.
    if any(
        marker in body.get_text(" ", strip=True)
        for marker in ("付费后阅读", "购买后阅读", "购买后查看全文", "试看结束")
    ):
        raise WeReadError("partial_body", "当前只获得预览或付费提示，未作为完整文章导出。")
    title_node = soup.find("meta", property="og:title") or soup.find(
        "meta", attrs={"name": "og:title"}
    )
    title = clean_label(title_node.get("content")) if title_node else ""
    if not title:
        title_element = soup.find(id="activity-name")
        title = clean_label(title_element.get_text()) if title_element else ""
    if not title or (expected_title and title != clean_label(expected_title)):
        raise WeReadError("title_mismatch", "正文标题与所选文章不一致，已停止保存。")
    for node in body.find_all("br"):
        node.replace_with("\n")
    for node in body.find_all(
        ["p", "div", "section", "h1", "h2", "h3", "h4", "li", "blockquote", "tr"]
    ):
        node.insert_before("\n")
        node.insert_after("\n")
    text = body.get_text().replace("\xa0", " ")
    text = "\n".join(re.sub(r"[\t\r ]+", " ", line).strip() for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    characters = len(re.sub(r"\s+", "", text))
    if characters < 200:
        raise WeReadError(
            "short_body", "提取到的正文少于200字，可能是短文或不完整页面，请在官方客户端核对。"
        )
    return {"title": title, "text": text, "characters": characters}


class WeReadClient:
    def __init__(self, auth_path: Path | None = None):
        self.auth_path = auth_path
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": UA, "Accept": "application/json, text/plain, */*"}
        )
        self.name = ""
        self.vid = ""
        self.api_key = ""
        self.extra_headers: dict[str, str] = {}
        self.validated = False
        self.recovery_used = False
        self.trace: list[dict[str, Any]] = []

    @property
    def logged_in(self):
        return bool(self.vid and self.session.cookies.get_dict().get("wr_skey"))

    def normalize_cookies(self, fresh=()):
        # Only a response from the allowed WeRead host can replace this session.
        selected = {}
        for cookie in [*cast(Any, self.session.cookies), *fresh]:
            if cookie.domain.lstrip(".") == "weread.qq.com":
                selected[cookie.name] = cookie
        for cookie in list(cast(Any, self.session.cookies)):
            if cookie.domain.lstrip(".") == "weread.qq.com":
                self.session.cookies.clear(cookie.domain, cookie.path, cookie.name)
        for cookie in selected.values():
            self.session.cookies.set_cookie(copy.copy(cookie))

    def prepare(self):
        try:
            self.validate()
        except WeReadError as error:
            if error.code != "-2012":
                raise
            self.recover(error.code)
        self.save()

    def recover(self, code):
        if self.recovery_used:
            raise WeReadError(code, "本任务登录恢复已尝试一次，停止重试。")
        self.recovery_used = True
        if code == "-2012":
            self.renew()
        else:
            try:
                self.validate()
            except WeReadError as error:
                if error.code not in {"-2012", "login_expired"}:
                    raise
                self.save()

    def request(self, method, path, **kwargs):
        started = time.monotonic()
        try:
            try:
                result = self._request(method, path, **kwargs)
            except WeReadError as error:
                if (path not in {"/web/mp/articles", "/web/mp/content", "/web/book/info"}
                        or error.code not in {"-2012", "-2041"} or self.recovery_used):
                    raise
                self.trace.append({"phase": path, "error_code": error.code})
                self.recover(error.code)
                kwargs["headers"] = {**kwargs.get("headers", {}), **self.extra_headers}
                result = self._request(method, path, **kwargs)
            self.trace.append({"phase": path, "ok": True,
                               "elapsed_s": round(time.monotonic() - started, 3),
                               "recovery_used": self.recovery_used})
            return result
        except WeReadError as error:
            self.trace.append({"phase": path, "error_code": error.code,
                               "elapsed_s": round(time.monotonic() - started, 3)})
            raise

    def _request(
        self,
        method,
        path,
        *,
        host="weread.qq.com",
        raw=False,
        params=None,
        payload=None,
        headers=None,
        timeout=25,
        session=None,
    ):
        if path not in ALLOWED.get(host, set()):
            raise WeReadError("invalid_endpoint", "此工具只请求微信读书自身的读取接口。")
        client = session or self.session
        previous_cookies = self.session.cookies.get_dict()
        try:
            with client.request(
                method,
                "https://" + host + path,
                params=params,
                json=payload,
                headers=headers,
                timeout=timeout,
                allow_redirects=False,
                stream=True,
            ) as response:
                status = response.status_code
                if 300 <= status < 400:
                    raise WeReadError(
                        "redirect",
                        "服务要求跳转或验证，工具未跟随跳转。请在官方客户端检查登录状态。",
                    )
                if status in (401, 403):
                    raise WeReadError(
                        "access_denied",
                        f"微信读书拒绝了请求（HTTP {status}），请检查账号登录或访问权限。",
                    )
                if status == 429:
                    raise WeReadError("rate_limited", "请求过于频繁，请稍后再试。")
                if status != 200:
                    raise WeReadError("http_error", f"微信读书返回 HTTP {status}。")
                chunks, size = [], 0
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > 16 * 1024 * 1024:
                        raise WeReadError("response_too_large", "单篇页面超过16 MB，本次没有保存。")
                    chunks.append(chunk)
                text = b"".join(chunks).decode("utf-8", errors="replace")
                response_headers = dict(response.headers)
                response_cookies = list(response.cookies)
        except requests.Timeout:
            raise WeReadError("timeout", "微信读书响应超时，请稍后重试。") from None
        except requests.RequestException:
            raise WeReadError("network", "无法连接微信读书，请检查网络。") from None
        if client is self.session:
            self.normalize_cookies(response_cookies)
            changed = [k for k, v in self.session.cookies.get_dict().items()
                       if previous_cookies.get(k) != v]
            if changed:
                self.trace.append({"phase": path, "cookie_names_changed": changed})
        if raw and not text.lstrip().startswith("{"):
            return text, response_headers, response_cookies
        try:
            data = json.loads(text)
        except ValueError:
            raise WeReadError(
                "not_json", "接口返回了非预期页面，可能需要在官方客户端完成验证。"
            ) from None
        if not isinstance(data, dict):
            raise WeReadError("unknown_response", "微信读书返回了未知格式。")
        code = data.get("errCode", data.get("errcode", 0))
        if code not in (0, "0", None):
            message = {
                "-2012": "登录已过期，请重新扫码。",
                "-2041": "微信读书没有接受读取凭证，请在官方客户端打开该公众号后重试。",
            }.get(str(code), f"微信读书返回错误码 {code}。")
            raise WeReadError(str(code), message)
        return (text if raw else data), response_headers, response_cookies

    def create_login(self):
        self.request("GET", "/r/weread-skills", raw=True, headers={"Referer": BASE + "/"})
        data, _, _ = self.request("GET", "/api/auth/getLoginUid", headers={"Referer": SKILLS})
        uid = data.get("uid")
        if not isinstance(uid, str) or not re.fullmatch(r"[A-Za-z0-9-]{16,100}", uid):
            raise WeReadError("login_format", "微信读书没有返回有效登录二维码。")
        return uid

    def poll_login(self, uid, otp=""):
        # Upstream uses a bare &otp for the initial poll.
        query = urlencode({"uid": uid}) + ("&" + urlencode({"otp": otp}) if otp else "&otp")
        data, _, _ = self.request(
            "GET", "/api/auth/getLoginInfo", params=query, headers={"Referer": SKILLS}, timeout=70
        )
        return data

    def finish_login(self, data):
        vid, access = str(data.get("webLoginVid") or ""), str(data.get("accessToken") or "")
        if not vid or not access:
            raise WeReadError("login_format", "登录确认缺少必要凭证，请重新扫码。")
        self.vid = vid
        self.session.cookies.clear()
        values = {"wr_vid": vid, "wr_skey": access, "wr_ql": "0"}
        if data.get("refreshToken"):
            values["wr_rt"] = quote(str(data["refreshToken"]), safe="")
        for key, value in values.items():
            self.session.cookies.set(key, value, domain=".weread.qq.com", path="/")
        self.validate()
        key_info, _, _ = self.request(
            "GET", "/api/skills/apikeyGet", params={"only_show": 1}, headers={"Referer": SKILLS}
        )
        self.api_key = key_info.get("apikey") or ""
        self.renew()
        self.save()

    def validate(self):
        if not self.logged_in:
            raise WeReadError("login_required", "请先扫码登录微信读书。")
        self.validated = False
        self.normalize_cookies()
        cookie = self.session.cookies.get_dict()
        data, _, _ = self.request(
            "GET",
            "/api/userInfo",
            params={"userVid": self.vid},
            headers={"X-Vid": self.vid, "X-Skey": cookie["wr_skey"], "Referer": SKILLS},
        )
        if not isinstance(data.get("name"), str) or not data["name"]:
            self.validated = False
            raise WeReadError("login_expired", "无法确认当前登录账号，请重新扫码。")
        self.name = data["name"]
        self.validated = True

    def renew(self):
        old = copy.deepcopy(self.session.cookies)
        old_headers = self.extra_headers.copy()
        self.validated = False
        try:
            data, headers, cookies = self.request(
                "POST", "/web/login/renewal",
                payload={"rq": "%2Fweb%2Fbook%2Fread", "ql": False},
                headers={"Origin": BASE, "Referer": BASE + "/"},
            )
            if data.get("succ") not in (True, 1, "1"):
                raise WeReadError("renewal_failed", "登录续期失败，请重新扫码。")
            self.normalize_cookies(cookies)
            for name, value in headers.items():
                if name.lower() in {"x-wr-ticket", "x-wrpa-0"} and value:
                    self.extra_headers[name.lower()] = value
            self.validate()
        except Exception:
            self.session.cookies = old
            self.extra_headers = old_headers
            self.validated = False
            raise
        self.save()

    def gateway(self, api_name, **params):
        if not self.api_key:
            raise WeReadError(
                "skill_not_enabled",
                "该账号尚未启用微信读书 Skill：请在官方 App 的「我 → 设置 → 微信读书 Skill」中获取 API Key，再重新登录。",
            )
        with requests.Session() as gateway:
            gateway.headers.update(
                {
                    "User-Agent": UA,
                    "Accept": "application/json",
                    "Authorization": "Bearer " + self.api_key,
                    "Origin": BASE,
                    "Referer": BASE + "/",
                }
            )
            data, _, _ = self.request(
                "POST",
                "/api/agent/gateway",
                host="i.weread.qq.com",
                session=gateway,
                payload={"api_name": api_name, "skill_version": "1.0.5", **params},
            )
        if data.get("upgrade_info"):
            raise WeReadError("upgrade_required", "微信读书要求更新接口版本，请先更新工具。")
        return data

    def search(self, query):
        data = self.gateway("/store/search", keyword=query, scope=2, count=20)
        if not isinstance(data.get("results"), list):
            raise WeReadError("search_format", "搜索返回格式发生变化，未把它当作没有结果。")
        accounts = parse_accounts(data)
        # Only public metadata field names are exposed for support diagnostics.
        shape = [
            {"title": clean_label(g.get("title")), "fields": list(g), "scope": g.get("scope")}
            for g in data.get("results", [])
            if isinstance(g, dict)
        ]
        return accounts, shape

    def shelf(self):
        data = self.gateway("/shelf/sync")
        if not isinstance(data.get("books"), list):
            raise WeReadError("shelf_format", "书架返回格式发生变化，未把它当作空书架。")
        return parse_accounts(data["books"])

    def account_info(self, account_id):
        if not ACCOUNT_RE.fullmatch(account_id):
            raise WeReadError("invalid_account", "公众号编号格式不正确。")
        data, _, _ = self.request(
            "GET", "/web/book/info", params={"bookId": account_id}, headers={"Referer": BASE + "/"}
        )
        candidates = parse_accounts(data)
        for candidate in candidates:
            if candidate["id"] == account_id:
                return candidate
        # Some versions omit the echoed ID but return the info object directly.
        title = clean_label(data.get("title"))
        if title:
            return {"id": account_id, "name": title, "intro": clean_label(data.get("intro"))}
        raise WeReadError("account_format", "无法核对公众号名称，未继续读取。")

    def catalog(self, account_id):
        if not ACCOUNT_RE.fullmatch(account_id):
            raise WeReadError("invalid_account", "公众号编号格式不正确。")
        data, _, _ = self.request(
            "GET", "/web/mp/articles",
            params={"bookId": account_id, "maxIdx": 0, "count": 100},
            headers={"Referer": BASE + "/", **self.extra_headers},
        )
        return parse_articles(data, account_id)

    def article(self, article):
        if not REVIEW_RE.fullmatch(article["id"]):
            raise WeReadError("invalid_article", "文章编号格式不正确。")
        source, _, _ = self.request(
            "GET",
            "/web/mp/content",
            params={"reviewId": article["id"]},
            raw=True,
            headers={
                "Referer": BASE + "/",
                "Accept": "text/html,application/xhtml+xml,*/*",
                **self.extra_headers,
            },
        )
        if source.lstrip().startswith("{"):
            try:
                error = json.loads(source)
                code = str(error.get("errCode", error.get("errcode", "content_format")))
            except (ValueError, AttributeError):
                code = "content_format"
            message = "微信读书没有返回文章网页，请检查登录状态后重试。"
            if code in {"-2012", "-2041"}:
                message = "微信读书没有接受当前读取凭证，请重新登录后重试。"
            raise WeReadError(code, message)
        return extract_article(source, article["title"])

    def save(self):
        if self.auth_path and self.logged_in and self.validated:
            atomic_json(
                self.auth_path,
                {
                    "name": self.name,
                    "vid": self.vid,
                    "api_key": self.api_key,
                    "cookies": [
                        {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
                        for c in self.session.cookies
                    ],
                    "extra_headers": self.extra_headers,
                    "saved_at": int(time.time()),
                },
            )

    def load(self):
        if not self.auth_path or not self.auth_path.exists():
            return False
        try:
            data = json.loads(self.auth_path.read_text(encoding="utf-8"))
            if any(not isinstance(data.get(key, ""), str) for key in ("name", "vid", "api_key")):
                raise ValueError("Invalid local credential types")
            self.name, self.vid, self.api_key = data["name"], data["vid"], data.get("api_key", "")
            self.extra_headers = {
                k: v
                for k, v in data.get("extra_headers", {}).items()
                if k in {"x-wr-ticket", "x-wrpa-0"}
            }
            for cookie in data["cookies"]:
                if cookie.get("domain", "").lstrip(".") != "weread.qq.com":
                    continue
                self.session.cookies.set(
                    cookie["name"],
                    cookie["value"],
                    domain=cookie["domain"],
                    path=cookie.get("path", "/"),
                )
            self.auth_path.chmod(0o600)
            return self.logged_in
        except (OSError, ValueError, KeyError, TypeError):
            self.name = self.vid = self.api_key = ""
            self.session.cookies.clear()
            return False
