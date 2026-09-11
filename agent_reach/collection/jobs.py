"""Durable media jobs. Discovery and semantic review belong to the calling Agent."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

from .weread_helper.file_lock import exclusive_lock


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def date(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("时间必须包含时区")
    return result


def select(manifest: dict, limit: int | None, all_available: bool) -> dict:
    mode = manifest.get("mode")
    if mode not in {"account", "keyword"}:
        raise ValueError("mode 必须为 account 或 keyword")
    if limit is not None and all_available:
        raise ValueError("数量与全量不能同时指定")
    if limit is None and not all_available:
        limit = 5 if mode == "keyword" else 20
    if limit is not None and (type(limit) is not int or limit <= 0):
        raise ValueError("数量必须是正整数")
    captured = date(manifest["captured_at"])
    seen, rows, excluded = set(), [], []
    for item in manifest["items"]:
        vid = str(item["video_id"])
        if not re.fullmatch(r"\d{10,25}", vid):
            raise ValueError("视频编号无效")
        if vid in seen:
            continue
        seen.add(vid)
        if mode == "keyword":
            try:
                published = date(item["published_at"])
                recent = captured - timedelta(hours=24) <= published <= captured
            except (KeyError, ValueError, TypeError):
                recent = False
            if not recent:
                excluded.append({"video_id": vid, "reason": "缺少可核验的24小时内发布时间"})
                continue
        rows.append({**item, "video_id": vid, "url": f"https://www.douyin.com/video/{vid}"})
    chosen = rows if all_available else rows[:limit]
    ended = manifest.get("discovery_complete") is True and bool(manifest.get("end_evidence"))
    fulfilled = bool(ended) if all_available else len(chosen) == limit
    return {
        "items": chosen,
        "excluded": excluded,
        "selected": len(chosen),
        "requested": "all" if all_available else limit,
        "selection_fulfilled": fulfilled,
        "discovery_complete": bool(ended),
        "mode": mode,
        "query": manifest.get("query", ""),
        "captured_at": manifest["captured_at"],
    }


def download(url: str, destination: Path) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not (parsed.hostname or "").endswith(".douyinvod.com")
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
    ):
        raise ValueError("媒体地址须来自已查看的抖音公开播放器")
    partial = destination.with_suffix(".part")
    size = 0
    try:
        with requests.get(url, timeout=(15, 40), stream=True, allow_redirects=False) as response:
            if response.status_code != 200 or "video/" not in response.headers.get(
                "Content-Type", ""
            ):
                raise ValueError("媒体未返回视频，不跟随重定向")
            with partial.open("wb") as stream:
                for block in response.iter_content(1024 * 1024):
                    size += len(block)
                    if size > 512 * 1024 * 1024:
                        raise ValueError("单个视频超过512MB，保留任务等待处理")
                    stream.write(block)
        if not size:
            raise ValueError("媒体为空")
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)


def state_status(state: dict) -> None:
    items = state["items"]
    state["completed"] = sum(r["status"] == "complete" for r in items)
    state["prepared"] = sum(r["status"] in {"complete", "awaiting_analysis"} for r in items)
    if items and state["completed"] == len(items):
        state["status"] = "complete" if state["selection_fulfilled"] else "partial"
    elif items and state["prepared"] == len(items):
        state["status"] = "awaiting_analysis"
    else:
        state["status"] = "partial"


def collect(manifest: dict, output: Path, *, limit=None, all_available=False, use_get=True) -> dict:
    selected = select(manifest, limit, all_available)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(output / "collection.lock"):
        return _collect_locked(selected, output, use_get=use_get)


def _collect_locked(selected: dict, output: Path, *, use_get: bool) -> dict:
    identity = {k: selected[k] for k in ["requested", "mode", "query", "captured_at"]}
    identity["ids"] = [i["video_id"] for i in selected["items"]]
    path = output / "job.json"
    if path.exists():
        state = json.loads(path.read_text())
        if state["identity"] != identity:
            raise ValueError("输出目录属于其他采集任务，请使用新目录")
    else:
        state = {
            **selected,
            "identity": identity,
            "platform": "douyin",
            "status": "collecting",
            "items": [
                {
                    "video_id": r["video_id"],
                    "source": {
                        k: v
                        for k, v in r.items()
                        if k not in {"media_url", "local_video", "get_record"}
                    },
                    "status": "pending",
                }
                for r in selected["items"]
            ],
        }
        save(path, state)
    for item, record in zip(selected["items"], state["items"]):
        if record["status"] == "complete":
            continue
        vid = item["video_id"]
        folder = output / vid
        if folder.is_symlink():
            raise ValueError("任务子目录不能是符号链接")
        folder.mkdir(exist_ok=True)
        get_dir = folder / "get"
        get_dir.mkdir(exist_ok=True)
        get_path = get_dir / f"{vid}.json"
        try:
            if not get_path.exists() and item.get("get_record"):
                old = json.loads(Path(item["get_record"]).read_text())
                if old.get("status") != "original_returned" or vid not in old.get("url", ""):
                    raise ValueError(
                        "导入Get记录必须有同一视频编号和原文；短链接须先提供核对后的规范记录"
                    )
                for field, data in old["fields"].items():
                    target = get_dir / f"{vid}-{field}.txt"
                    shutil.copyfile(data["file"], target)
                    if data.get("sha256") and digest(target) != data["sha256"]:
                        raise ValueError("导入原文哈希不匹配")
                    data["file"] = str(target)
                old["label"] = vid
                save(get_path, old)
            if use_get and (
                not get_path.exists()
                or json.loads(get_path.read_text()).get("status") != "original_returned"
            ):
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "agent_reach.collection.get_client",
                        str(get_dir),
                        vid,
                        item["url"],
                    ],
                    check=False,
                )
            if not get_path.exists():
                raise ValueError("等待Get原文记录")
            get = json.loads(get_path.read_text())
            if get.get("status") != "original_returned" or not get.get("fields"):
                raise ValueError("Get原文尚未就绪；保留任务编号以供续查")
            record["originals"] = get["fields"]
            for original in record["originals"].values():
                if original.get("sha256") and digest(Path(original["file"])) != original["sha256"]:
                    raise ValueError("原文字段与已保存文件不一致")
            record["quality_warning"] = get.get("quality_warning")
            owned = folder / "video.mp4"
            if item.get("local_video"):
                video = Path(item["local_video"]).resolve()
                record["video_owned"] = False
            else:
                video = owned
                if video.exists() and not record.get("video_owned"):
                    raise ValueError("任务中存在未登记视频，不能覆盖或认领")
                if not video.exists():
                    if not item.get("media_url"):
                        record["status"] = "needs_browser_media"
                        save(path, state)
                        continue
                    record["video_owned"] = True
                    record["video_file"] = str(video)
                    save(path, state)
                    download(item["media_url"], video)
            record["video_file"] = str(video)
            frames_path = video.with_suffix("") / "frames.json"
            # Never write extracted frames next to a user-owned video.
            if not record["video_owned"]:
                frames_path = folder / "frames" / "frames.json"
            from agent_reach.collection.frames import extract

            if not frames_path.exists():
                extract(video, root=frames_path.parent)
            frames = json.loads(frames_path.read_text())
            if frames["sha256"] != digest(video):
                raise ValueError("视频已变化，不能复用旧画面")
            expected = item.get("duration_s")
            if expected is not None and (
                not math.isfinite(float(expected))
                or abs(frames["duration_s"] - float(expected)) > 2
            ):
                raise ValueError("页面时长与媒体时长不符")
            record.update(
                status="awaiting_analysis",
                frames=str(frames_path),
                video_sha256=frames["sha256"],
                error=None,
            )
        except Exception as error:
            record.update(
                status="partial",
                error={
                    "type": type(error).__name__,
                    "message": str(error)
                    if isinstance(error, ValueError)
                    else "处理失败，保留任务；检查登录、网络或可选媒体依赖",
                },
            )
        state_status(state)
        save(path, state)
    state_status(state)
    save(path, state)
    return state


def finalize(output: Path, review: dict) -> dict:
    """Validate Agent-authored evidence. This does not itself perform semantic review."""
    output = output.resolve()
    path = output / "job.json"
    state = json.loads(path.read_text())
    if state.get("platform") == "wechat":
        article = next(r for r in state["items"] if r["id"] == review["article_id"])
        if article["status"] not in {"body_saved", "complete"}:
            raise ValueError("该文章尚未保存正文")
        body = Path(article["file"])
        if digest(body) != article["sha256"] or review["quote"] not in body.read_text():
            raise ValueError("正文变化或引用不在正文中")
        for key in ["conclusion", "value", "structure", "doubts"]:
            if not isinstance(review.get(key), str) or not review[key].strip():
                raise ValueError(f"缺少分析字段：{key}")
        report = output / (article["id"] + "-analysis.md")
        report.write_text(
            f"# {article['title']}\n\n"
            + "\n\n".join(
                [
                    f"**{name}：**{review[key]}"
                    for key, name in [
                        ("conclusion", "结论"),
                        ("value", "价值"),
                        ("structure", "结构"),
                        ("doubts", "疑点"),
                    ]
                ]
            )
            + f"\n\n[原文]({body})\n\n> {review['quote']}\n"
        )
        article.update(status="complete", report=str(report))
        state["completed"] = sum(a["status"] == "complete" for a in state["items"])
        state["status"] = (
            "complete"
            if state["completed"] == len(state["items"]) and state["selection_fulfilled"]
            else "partial"
        )
        save(path, state)
        return state
    vid = str(review["video_id"])
    record = next(r for r in state["items"] if r["video_id"] == vid)
    if record["status"] not in {"awaiting_analysis", "complete"}:
        raise ValueError("该视频尚未备齐原文与画面")
    frames_path = Path(record["frames"])
    frames = json.loads(frames_path.read_text())
    chosen = review.get("reviewed_frames", [])
    allowed = {r["file"]: r for r in frames["frames"]}
    if not chosen or any(
        f not in allowed or not (frames_path.parent / f).is_file() for f in chosen
    ):
        raise ValueError("必须引用已取得的真实画面")
    for key in ["conclusion", "value", "structure", "visual", "doubts", "quote", "original_field"]:
        if not isinstance(review.get(key), str) or not review[key].strip():
            raise ValueError(f"缺少分析字段：{key}")
    if review.get("identity_verified") is not True:
        raise ValueError("Agent须核对作者、标题与视频对应关系")
    original = record["originals"][review["original_field"]]
    source = Path(original["file"])
    if original.get("sha256") and digest(source) != original["sha256"]:
        raise ValueError("原文已变化")
    text = source.read_text()
    if review["quote"] not in text:
        raise ValueError("引用不在原文中")
    title = record["source"].get("title", record["source"].get("headline", vid))
    lines = [
        f"# {title}",
        "",
        f"来源：{record['source'].get('author', '待核对')}｜{record['source']['url']}",
        "",
    ]
    for key, name in [
        ("conclusion", "结论"),
        ("value", "内容价值"),
        ("structure", "内容结构"),
        ("visual", "画面表达"),
        ("doubts", "疑点与边界"),
    ]:
        lines += [f"**{name}：**{review[key]}", ""]
    lines += [
        f"[Get原文]({source})；以下是文字引用，不是逐句时间戳：",
        "",
        "> " + review["quote"],
        "",
        "已检查的画面：",
        "",
    ]
    lines += [f"- [{allowed[f]['actual_s']:.2f}秒]({frames_path.parent / f})" for f in chosen]
    lines += ["", "本报告由Agent结合原文和抽样画面生成，非逐帧、非完整音轨核验。"]
    report = output / vid / "report.md"
    report.write_text("\n".join(lines) + "\n")
    save(output / vid / "review.json", review)
    record.update(status="complete", report=str(report), reviewed_frames=chosen)
    state_status(state)
    save(path, state)
    # Only a file explicitly created by this task may be removed after evidence is saved.
    video = Path(record["video_file"])
    owned = output / vid / "video.mp4"
    if (
        record.get("video_owned")
        and video == owned
        and not video.is_symlink()
        and video.parent.resolve() == output / vid
        and video.exists()
    ):
        if digest(video) != record["video_sha256"]:
            raise ValueError("视频已被替换，未执行清理")
        video.unlink()
        record["video_cleaned"] = True
        save(path, state)
    return state
