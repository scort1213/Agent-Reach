"""Read X threads and resolve link-only X Articles using the existing OpenCLI."""

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .jobs import digest, save
from .weread_helper.file_lock import exclusive_lock


def tweet_id(source: str) -> str:
    match = re.fullmatch(r"(?:https://(?:x|twitter)\.com/[A-Za-z0-9_]+/status/)?([0-9]{10,25})/?", source)
    if not match:
        raise ValueError("需要 X 帖子编号或完整 status 链接")
    return match[1]


def call(command: str, ident: str, output: Path) -> list:
    argv = ["opencli", "twitter", command, ident, "-f", "json"]
    if command == "thread":
        argv += ["--limit", "10"]
    result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", timeout=120)
    (output / f"{command}.json").write_text(result.stdout, encoding="utf-8")
    (output / f"{command}.err").write_text(result.stderr, encoding="utf-8")
    if result.returncode:
        raise ValueError(f"OpenCLI {command} 失败，详情保留在任务目录")
    data = json.loads(result.stdout)
    if not isinstance(data, list):
        raise ValueError("OpenCLI 返回的记录格式无效")
    return data


def collect(source: str, output: Path, resume: bool = False) -> dict:
    ident = tweet_id(source)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(output / "collection.lock"):
        path = output / "job.json"
        if path.exists():
            old = json.loads(path.read_text(encoding="utf-8"))
            if old["source_id"] != ident:
                raise ValueError("任务目录属于另一条帖子")
            if resume:
                for file, sha in old.get("hashes", {}).items():
                    if digest(output / file) != sha:
                        raise ValueError("保存的正文已变化，需重新读取")
                return {**old, "round_mode": "saved_resume"}
        elif resume:
            raise ValueError("没有可续跑的任务")
        attempt = {"platform": "twitter", "source_id": ident, "status": "collecting",
                   "captured_at": datetime.now(timezone.utc).isoformat(),
                   "actual_entry": "opencli"}
        # A new failed request must not leave an older successful job visible.
        save(path, attempt)
        try:
            rows = call("thread", ident, output)
        except (ValueError, OSError, subprocess.TimeoutExpired) as error:
            save(path, {**attempt, "status": "failed", "error_type": type(error).__name__})
            raise
        root = next((r for r in rows if str(r.get("id")) == ident), None)
        if root is None:
            save(path, {**attempt, "status": "failed", "error_type": "source_mismatch"})
            raise ValueError("返回的帖子与目标编号不符")
        body = root.get("text", "").strip()
        kind = "tweet"
        diagnostics = []
        if not re.sub(r"https?://\S+", "", body).strip():
            try:
                articles = call("article", ident, output)
                article = next((r for r in articles if tweet_id(r.get("url", "")) == ident), None)
                if article and article.get("content", "").strip():
                    body = f"# {article.get('title', '')}\n\n{article['content']}"
                    kind = "x_article"
            except (ValueError, subprocess.TimeoutExpired) as error:
                diagnostics.append(str(error))
        meaningful = bool(re.sub(r"https?://\S+", "", body).strip())
        (output / "body.md").write_text(body + "\n", encoding="utf-8")
        state = {
            "platform": "twitter", "source_id": ident, "source_url": root.get("url"),
            "captured_at": datetime.now(timezone.utc).isoformat(), "actual_entry": "opencli",
            "status": "awaiting_analysis" if meaningful else "unresolved_link",
            "content_kind": kind, "reply_count": len(rows) - 1,
            "reply_scope": "当前返回最多10条记录，不代表全部回复", "diagnostics": diagnostics,
            "hashes": {f: digest(output / f) for f in ("body.md", "thread.json")},
        }
        save(path, state)
        return state
