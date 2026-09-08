"""Keyword listings via the pinned wechat-article-search parser and Sogou.

No article-body requests, login-cookie extraction, or implicit installation.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import requests

from agent_reach.utils.paths import home_dir

ASSETS = Path(__file__).parent / "vendor" / "wechat_article_search"
MAX_HTML_BYTES = 2 * 1024 * 1024
SETUP_HINT = "请先运行 agent-reach setup-wechat（需要 Node.js 20.18.1+ 和 npm）。"


class WeChatSearchError(RuntimeError):
    """A failed or blocked search, never equivalent to an empty listing."""


def runtime_dir() -> Path:
    return home_dir() / ".agent-reach" / "tools" / "wechat-article-search"


def _run_parser(html: str | None = None, limit: int = 10) -> subprocess.CompletedProcess[str]:
    node = shutil.which("node")
    if not node:
        raise WeChatSearchError(SETUP_HINT)
    env = dict(os.environ)
    # Use only this optional runtime, not another globally installed parser.
    env["NODE_PATH"] = str(runtime_dir() / "node_modules")
    try:
        return subprocess.run(
            [node, str(ASSETS / "run.cjs"), "--probe" if html is None else str(limit)],
            input=html, capture_output=True, text=True, encoding="utf-8",
            timeout=10, env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WeChatSearchError("公众号解析器无法运行。" + SETUP_HINT) from exc


def probe_runtime() -> bool:
    if not (runtime_dir() / "node_modules" / "cheerio" / "package.json").is_file():
        return False
    try:
        result = _run_parser()
        return result.returncode == 0 and result.stdout.strip() == "parser-ready"
    except WeChatSearchError:
        return False


def setup_runtime() -> None:
    """Explicitly install only the locked optional npm dependency tree."""
    npm = shutil.which("npm")
    if not npm or not shutil.which("node"):
        raise WeChatSearchError(SETUP_HINT)
    target = runtime_dir()
    target.mkdir(parents=True, exist_ok=True)
    for name in ("package.json", "package-lock.json"):
        shutil.copyfile(ASSETS / name, target / name)
    try:
        result = subprocess.run(
            [npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
            cwd=target, capture_output=True, text=True, timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WeChatSearchError("公众号搜索依赖安装未完成。" + SETUP_HINT) from exc
    if result.returncode or not probe_runtime():
        raise WeChatSearchError("公众号搜索依赖安装或解析检查失败。" + SETUP_HINT)


def _fetch_listing(query: str, page: int) -> str:
    try:
        with requests.get(
            "https://weixin.sogou.com/weixin",
            params={"query": query, "type": "2", "page": str(page), "ie": "utf8"},
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
                "Accept": "text/html", "Accept-Language": "zh-CN,zh;q=0.9",
            },
            timeout=(10, 20), allow_redirects=False, stream=True,
        ) as response:
            if response.status_code != 200:
                raise WeChatSearchError(
                    f"搜狗返回 HTTP {response.status_code}，搜索未完成；未自动重试或跟随跳转。"
                )
            if "text/html" not in response.headers.get("Content-Type", "").lower():
                raise WeChatSearchError("搜狗返回了非网页内容，搜索未完成。")
            chunks = []
            size = 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > MAX_HTML_BYTES:
                    raise WeChatSearchError("搜索页超过大小上限，已停止读取。")
                chunks.append(chunk)
            return b"".join(chunks).decode("utf-8", errors="replace")
    except requests.RequestException as exc:
        raise WeChatSearchError("连接搜狗失败或超时，不能视为没有文章。") from exc


def search_wechat(query: str, limit: int = 10, page: int = 1) -> dict[str, Any]:
    query = query.strip()
    if not query or len(query) > 200:
        raise WeChatSearchError("请输入 1–200 个字符的关键词。")
    if not 1 <= limit <= 10 or not 1 <= page <= 100:
        raise WeChatSearchError("每页数量须为 1–10；页码须为 1–100。")
    if not probe_runtime():
        raise WeChatSearchError(SETUP_HINT)
    result = _run_parser(_fetch_listing(query, page), limit)
    if result.returncode:
        raise WeChatSearchError(result.stderr.strip()[:500] or "搜索页解析失败。")
    try:
        rows = json.loads(result.stdout)
        if not isinstance(rows, list):
            raise ValueError("expected article list")
    except (ValueError, TypeError) as exc:
        raise WeChatSearchError("公众号解析器没有返回有效结果。") from exc
    return {
        "status": "ok", "backend": "wechat-article-search", "query": query,
        "page": page, "total": len(rows), "articles": rows,
        "scope": "sogou_search_listing", "body_fetched": False,
        "note": "仅搜狗收录的搜索结果；摘要不是正文，不保证搜全或覆盖账号完整历史。",
    }
