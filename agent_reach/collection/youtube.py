"""Resumable YouTube text preparation; semantic and visual review stays with Agent."""

import contextlib
import io
import json
import math
import re
import subprocess
from pathlib import Path

from . import get_client
from .jobs import digest, save
from .weread_helper.file_lock import exclusive_lock


def canonical(source: str) -> tuple[str, str]:
    match = re.fullmatch(r"https://(?:www\.youtube\.com/watch\?v=|youtu\.be/)([A-Za-z0-9_-]{11})/?", source)
    if not match:
        raise ValueError("需要单条YouTube watch或youtu.be链接")
    return match[1], "https://www.youtube.com/watch?v=" + match[1]


def collect(source: str, output: Path, metadata: Path | None = None,
            use_get: bool = False, max_minutes: float = 3) -> dict:
    ident, url = canonical(source)
    if not math.isfinite(max_minutes) or max_minutes <= 0:
        raise ValueError("转写额度必须为正数")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(output / "collection.lock"):
        path = output / "job.json"
        if path.exists():
            state = json.loads(path.read_text(encoding="utf-8"))
            if state["source_id"] != ident:
                raise ValueError("任务目录属于另一条视频")
            if state.get("original"):
                original = state["original"]
                if digest(Path(original["file"])) != original["sha256"]:
                    raise ValueError("原文文件变化，不能直接续用")
                return state
        else:
            state = {"platform": "youtube", "source_id": ident, "source_url": url,
                     "status": "collecting", "attempts": [], "reserved_seconds": 0}
            save(path, state)
        if metadata is not None:
            meta = json.loads(metadata.read_text(encoding="utf-8"))
            if str(meta.get("id")) != ident or not meta.get("title"):
                raise ValueError("元数据与视频编号不符或缺少标题")
            state["metadata"] = meta
        if not state["attempts"]:
            try:
                result = subprocess.run(["opencli", "youtube", "transcript", url, "-f", "json"],
                                        capture_output=True, text=True, encoding="utf-8", timeout=120)
                (output / "native.json").write_text(result.stdout, encoding="utf-8")
                (output / "native.err").write_text(result.stderr, encoding="utf-8")
                state["attempts"].append({"entry": "opencli", "returncode": result.returncode})
                if re.search(r"access_denied|policy.denied|permission.denied|禁止访问|明确拒绝", result.stderr, re.I):
                    state["status"] = "blocked"
                    save(path, state)
                    return state
                if result.returncode == 0:
                    rows = json.loads(result.stdout)
                    if isinstance(rows, list):
                        text = "\n".join(str(r.get("text", "")) for r in rows if isinstance(r, dict)).strip()
                        if text:
                            target = output / "original.txt"
                            target.write_text(text, encoding="utf-8")
                            state.update(status="awaiting_analysis", original={
                                "file": str(target), "sha256": digest(target), "source": "native_captions"})
            except (subprocess.TimeoutExpired, OSError, ValueError) as error:
                state["attempts"].append({"entry": "opencli", "error_type": type(error).__name__})
            state["status"] = "awaiting_analysis" if state.get("original") else "needs_transcript"
            save(path, state)
        if state.get("original") or state.get("status") == "blocked" or not use_get:
            return state
        # The calling Agent must first resolve any actual policy denial; opting in
        # here is a routing choice, never a claim that a denied target was restored.
        duration = float(state.get("metadata", {}).get("duration", 0))
        if not math.isfinite(duration) or duration <= 0 or duration > max_minutes * 60:
            raise ValueError("Get提交前需要经过核对的时长，且不超过本任务额度")
        if not state["reserved_seconds"]:
            state["reserved_seconds"] = duration
            save(path, state)
        with contextlib.redirect_stdout(io.StringIO()):
            get_client.main([str(output / "get"), ident, url])
        record = json.loads((output / "get" / f"{ident}.json").read_text(encoding="utf-8"))
        state["get_record"] = str(output / "get" / f"{ident}.json")
        fields = record.get("fields", {})
        if fields:
            field = "audio.original" if "audio.original" in fields else "web_page.content"
            state.update(original={**fields[field], "source": "get." + field}, status="awaiting_analysis")
        else:
            state.update(status="needs_transcript", get_status=record.get("status"))
        state["analysis_note"] = "已准备文字，Agent仍须核对内容与视频对应关系并实际查看画面"
        save(path, state)
        return state
