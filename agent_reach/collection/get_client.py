"""Official Get link workflow with resumable task records, no local speech model.

CLI: get_pipeline.py OUTPUT_DIRECTORY LABEL SHARE_URL [LABEL SHARE_URL ...]
Existing task records are resumed, never silently resubmitted.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

if __package__:
    from .weread_helper.file_lock import exclusive_lock
else:  # Preserve direct script execution as well as python -m.
    from weread_helper.file_lock import exclusive_lock

BASE = "https://openapi.biji.com/open/api/v1"
CONFIG = Path.home() / "Library/Application Support/AgentReachGetNote/credentials.json"


def valid_douyin_url(url):
    return bool(
        re.fullmatch(
            r"https://(?:v\.douyin\.com/[A-Za-z0-9_-]+|www\.douyin\.com/video/[0-9]{10,25})/?", url
        )
    )


def original_fields(note):
    fields = {}
    for group, key in (("web_page", "content"), ("audio", "original")):
        value = (note.get(group) or {}).get(key)
        if isinstance(value, str) and value.strip():
            fields[f"{group}.{key}"] = value
    return fields


class Client:
    def __init__(self):
        self.config = json.loads(CONFIG.read_text())
        self.last_request = 0.0

    def request(self, path, payload=None, *, create=False):
        # Only the official origin receives credentials; redirects are not followed.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        opener = urllib.request.build_opener(NoRedirect())
        for attempt in range(3):
            time.sleep(max(0, 3 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            req = urllib.request.Request(
                BASE + path,
                data=json.dumps(payload).encode() if payload is not None else None,
                headers={
                    "Authorization": self.config["api_key"],
                    "X-Client-ID": self.config["client_id"],
                    "Content-Type": "application/json",
                },
            )
            try:
                with opener.open(req, timeout=40) as response:
                    body = json.load(response)
                    if body.get("success") is not True:
                        raise RuntimeError(
                            f"Get API rejected request: code={body.get('code')}; request_id={body.get('request_id')}"
                        )
                    return body["data"], {
                        "endpoint": path.split("?")[0],
                        "http": response.status,
                        "request_id": body.get("request_id"),
                        "at": time.time(),
                    }
            except urllib.error.HTTPError as error:
                if error.code == 429 and not create and attempt < 2:
                    retry = error.headers.get("Retry-After", "20")
                    time.sleep(min(60, max(20, int(retry) if retry.isdigit() else 20)))
                    continue
                raise RuntimeError(
                    f"Get HTTP {error.code}; {'create not retried' if create else 'read stopped'}"
                ) from None
            except (urllib.error.URLError, TimeoutError):
                raise RuntimeError(
                    "Get network failure; submission may have succeeded, not automatically retried"
                ) from None
        raise RuntimeError("Get retry limit")


def save(path, record):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    tmp.replace(path)


def main(args=None):
    args = sys.argv[1:] if args is None else args
    if len(args) < 3 or len(args) % 2 != 1:
        raise SystemExit("Usage: get_pipeline.py OUTPUT LABEL URL [LABEL URL ...]")
    root = Path(args[0])
    root.mkdir(parents=True, exist_ok=True)
    # The collection parent holds collection.lock (or podcast.lock), never
    # this lock. Concurrent direct invocations must share the Get state guard.
    with exclusive_lock(root / "get.lock"):
        return _main_locked(args, root)


def _main_locked(args, root):
    client = Client()
    pending = []
    for label, url in zip(args[1::2], args[2::2]):
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", label):
            raise ValueError("Invalid label")
        if not (valid_douyin_url(url) or re.fullmatch(r"https://www\.xiaoyuzhoufm\.com/episode/[a-f0-9]{24}/?", url)):
            raise ValueError("Expected official Douyin or public Xiaoyuzhou episode URL")
        path = root / (label + ".json")
        if path.exists():
            record = json.loads(path.read_text())
            if record["url"] != url:
                raise ValueError("Existing label belongs to another URL")
            if record.get("status") == "original_returned":
                print(json.dumps({"label": label, "status": "already_downloaded"}), flush=True)
                continue
            if not record.get("task_id") and not record.get("note_id"):
                print(
                    json.dumps({"label": label, "status": "submission_needs_reconciliation"}),
                    flush=True,
                )
                continue
        else:
            record = {
                "label": label,
                "url": url,
                "started_at": time.time(),
                "status": "submitting",
                "requests": [],
                "uses_local_asr": False,
                "review_status": "unreviewed",
            }
            save(path, record)
            try:
                data, trace = client.request(
                    "/resource/note/save", {"note_type": "link", "link_url": url}, create=True
                )
                record["requests"].append(trace)
                if data.get("tasks"):
                    record["task_id"] = str(data["tasks"][0]["task_id"])
                elif data.get("note_id"):
                    record["note_id"] = str(data["note_id"])
                else:
                    raise RuntimeError("Create response contains no task or note ID")
                record["status"] = "processing"
            except Exception as error:
                record.update(status="submission_needs_reconciliation", error=str(error))
                save(path, record)
                print(
                    json.dumps({"label": label, "status": record["status"], "error": str(error)}),
                    flush=True,
                )
                continue
            save(path, record)
        pending.append((path, record))
        print(
            json.dumps(
                {"label": label, "status": record["status"], "task_id": record.get("task_id")}
            ),
            flush=True,
        )
    deadline = time.monotonic() + 600
    while pending and time.monotonic() < deadline:
        for path, record in list(pending):
            try:
                if not record.get("note_id"):
                    data, trace = client.request(
                        "/resource/note/task/progress", {"task_id": record["task_id"]}
                    )
                    record["requests"].append(trace)
                    record["task_status"] = data.get("status")
                    if data.get("status") == "failed":
                        raise RuntimeError("Get task failed")
                    if data.get("status") != "success":
                        save(path, record)
                        continue
                    record["note_id"] = str(data["note_id"])
                    save(path, record)
                data, trace = client.request(
                    "/resource/note/detail?" + urllib.parse.urlencode({"id": record["note_id"]})
                )
                record["requests"].append(trace)
                note = data["note"]
                fields = original_fields(note)
                record.update(
                    title=note.get("title"),
                    note_type=note.get("note_type"),
                    source_url=(note.get("web_page") or {}).get("url"),
                    fields={},
                    elapsed_s=round(time.time() - record["started_at"], 1),
                )
                for field, text in fields.items():
                    target = root / (record["label"] + "-" + field + ".txt")
                    target.write_text(text)
                    chinese = len(re.findall(r"[\u4e00-\u9fff]", text))
                    record["fields"][field] = {
                        "characters": len(text),
                        "chinese_characters": chinese,
                        "sha256": hashlib.sha256(text.encode()).hexdigest(),
                        "file": str(target.resolve()),
                    }
                record["status"] = "original_returned" if fields else "no_original"
                record["quality_warning"] = (
                    "中文视频原文为空、过短或没有中文，需复核"
                    if not fields
                    or all(
                        len(t) < 100 or not re.search(r"[\u4e00-\u9fff]", t)
                        for t in fields.values()
                    )
                    else None
                )
                print(
                    json.dumps(
                        {
                            k: record.get(k)
                            for k in [
                                "label",
                                "status",
                                "title",
                                "note_id",
                                "elapsed_s",
                                "fields",
                                "quality_warning",
                            ]
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                pending.remove((path, record))
            except Exception as error:
                record.update(status="read_or_task_failed", error=str(error))
                print(json.dumps({"label": record["label"], "error": str(error)}), flush=True)
                pending.remove((path, record))
            save(path, record)
        if pending:
            time.sleep(20)
    for path, record in pending:
        record["status"] = "pending_timeout"
        save(path, record)
        print(
            json.dumps(
                {
                    "label": record["label"],
                    "status": "pending_timeout",
                    "task_id": record.get("task_id"),
                }
            ),
            flush=True,
        )
    # Transport completion is not evidence of a usable transcript.
    for label in args[1::2]:
        record = json.loads((root / (label + ".json")).read_text())
        if record.get("status") != "original_returned" or record.get("quality_warning"):
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
