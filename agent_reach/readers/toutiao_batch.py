"""Read a browser-discovered list; this module does not automate a browser."""
from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from agent_reach.channels.toutiao import format_article
from agent_reach.readers.toutiao import ToutiaoReadError, normalize_article_url, read_article


def listing_url(url: str) -> str:
    """Decode observed search links without following redirects or visiting them."""
    for _ in range(4):
        p = urlsplit(url)
        if p.scheme not in {"http", "https"} or p.username or p.password or p.port not in {None, 80, 443}:
            break
        if p.hostname in {"so.toutiao.com", "sou.toutiao.com"} and p.path == "/search/jump":
            values = parse_qs(p.query).get("url", [])
            if len(values) != 1:
                break
            url = values[0]
            continue
        if p.hostname in {"www.toutiao.com", "toutiao.com", "m.toutiao.com"}:
            match = re.fullmatch(r"/a(\d{10,25})/?", p.path)
            if match:
                url = f"https://www.toutiao.com/article/{match[1]}/"
            return normalize_article_url(url)
        break
    raise ValueError("列表中存在不支持的文章链接")


def _date(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("日期必须包含时区")
    return result


def _save(path: Path, value: Any) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_batch(manifest: dict[str, Any], output: Path, *, limit: int = 20,
               hours: float | None = None, interval: float = 1) -> dict[str, Any]:
    """Checkpoint each selected item. Resume only an identical discovery/filter run."""
    if manifest.get("mode") not in {"keyword", "account"}:
        raise ValueError("mode 应为 keyword 或 account")
    captured = _date(manifest["captured_at"])
    if manifest["mode"] == "keyword" and hours is None:
        hours = 24
    if limit < 1 or (hours is not None and hours <= 0):
        raise ValueError("limit 和 hours 必须大于零")
    raw = manifest.get("items")
    if not isinstance(raw, list) or len(raw) > 10000:
        raise ValueError("items 必须为最多10000条的列表")
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        url = listing_url(item["url"])
        if url in seen:
            continue
        seen.add(url)
        if len(selected) < limit:
            selected.append({**item, "url": url})
    identity = hashlib.sha256(json.dumps([manifest, limit, hours], sort_keys=True,
                                         ensure_ascii=False).encode()).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    state_path = output / "batch.json"
    records: list[dict[str, Any]] = []
    if state_path.exists():
        old = json.loads(state_path.read_text(encoding="utf-8"))
        if old.get("run_id") != identity:
            raise ValueError("输出目录属于其他列表或筛选条件，请使用新目录")
        records = old["items"]
    summary = {"run_id": identity, "discovery": manifest, "limit": limit, "hours": hours,
               "unique_discovered": len(seen), "selected": len(selected),
               "discovery_complete": manifest.get("discovery_complete") is True,
               "items": records, "processing_complete": False}
    _save(state_path, summary)
    # Failed items are deliberately retained; no silent repeated requests after a denial.
    stopped = any(r.get("error", {}).get("code") in {"access_denied", "rate_limited"}
                  for r in records)
    for index, item in enumerate(selected[len(records):], start=len(records)):
        record: dict[str, Any] = {"position": index + 1, "candidate": item}
        if stopped:
            record.update(status="not_attempted", reason="前序访问拒绝或限流，批次已停止")
        else:
            try:
                if index:
                    time.sleep(interval)
                article = read_article(item["url"])
                expected = re.sub(r"\s+", "", unicodedata.normalize("NFKC", item.get("title", "")))
                actual = re.sub(r"\s+", "", unicodedata.normalize("NFKC", article["title"]))
                if expected and expected != actual:
                    raise ToutiaoReadError("title_mismatch", "正文标题与列表标题不一致，未计入成功")
                status = "success"
                if hours is not None:
                    try:
                        published = _date(article["published_at"])
                    except (ValueError, TypeError, AttributeError):
                        status = "date_unknown"
                    else:
                        if not captured - timedelta(hours=hours) <= published <= captured:
                            status = "outside_window"
                if not article.get("text", "").strip():
                    status = "no_text_body"
                record.update(status=status, article=article)
                article_id = article["article_id"]
                _save(output / f"{article_id}.json", article)
                (output / f"{article_id}.md").write_text(format_article(article), encoding="utf-8")
            except ToutiaoReadError as exc:
                record.update(status="failed", error={"code": exc.code, "message": str(exc)})
                stopped = exc.code in {"access_denied", "rate_limited"}
        records.append(record)
        _save(state_path, summary)
    summary["processing_complete"] = len(records) == len(selected) and not stopped
    summary["successful_bodies"] = sum(r["status"] == "success" for r in records)
    summary["ok"] = summary["processing_complete"] and all(
        r["status"] in {"success", "outside_window"} for r in records)
    _save(state_path, summary)
    return summary
