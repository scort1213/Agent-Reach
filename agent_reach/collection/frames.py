"""Bounded local video decoding only. No speech/vision model is loaded."""

import hashlib
import json
import math
import sys
from pathlib import Path

import av
from PIL import Image, ImageDraw


def extract(path, root=None):
    path = Path(path)
    root = Path(root) if root is not None else path.with_suffix("")
    root.mkdir(parents=True, exist_ok=True)
    with av.open(str(path)) as c:
        s = c.streams.video[0]
        s.codec_context.thread_count = 1
        duration = float(s.duration * s.time_base) if s.duration else c.duration / av.time_base
        count = min(48, math.ceil(duration / 10))
        targets: list[float] = (
            [i * 10 for i in range(count)]
            if duration <= 480
            else [i * (duration - 0.1) / 47 for i in range(48)]
        )
        rows = []
        for index, target in enumerate(targets):
            c.seek(int(target / s.time_base), stream=s, backward=True)
            for frame in c.decode(s):
                actual = float(frame.time)
                if actual + 0.001 >= target:
                    image = frame.to_image()
                    image.thumbnail((1280, 1280))
                    out = root / f"frame-{index:03d}-{target:06.1f}s.jpg"
                    image.save(out, quality=88)
                    rows.append(
                        {"requested_s": target, "actual_s": round(actual, 3), "file": out.name}
                    )
                    break
        for begin in range(0, len(rows), 6):
            batch = rows[begin : begin + 6]
            sheet = Image.new("RGB", (1200, 3 * 720), "#111111")
            draw = ImageDraw.Draw(sheet)
            for pos, row in enumerate(batch):
                im = Image.open(root / row["file"])
                im.thumbnail((596, 680))
                x = (pos % 2) * 600
                y = (pos // 2) * 720
                draw.text((x + 10, y + 8), f"{path.stem} | {row['actual_s']:.2f}s", fill="white")
                sheet.paste(im, (x + (600 - im.width) // 2, y + 35))
            sheet.save(root / f"sheet-{begin // 6 + 1}.jpg", quality=90)
    report = {
        "video": str(path.resolve()),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "duration_s": duration,
        "frames": rows,
        "single_decode_thread": True,
        "asr_loaded": False,
        "base_frame_limit": 48,
        "analysis_status": "unreviewed",
    }
    (root / "frames.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(path.stem, "duration", round(duration, 3), "frames", len(rows), flush=True)


def supplement(video, frames_path, targets):
    """Append at most eight targeted frames in total, before video cleanup."""
    video, frames_path = Path(video), Path(frames_path)
    data = json.loads(frames_path.read_text())
    if hashlib.sha256(video.read_bytes()).hexdigest() != data["sha256"]:
        raise ValueError("视频已变化，不能补帧")
    used = sum(bool(row.get("supplemental")) for row in data["frames"])
    targets = list(dict.fromkeys(float(t) for t in targets))
    if not targets or used + len(targets) > 8:
        raise ValueError("每个视频累计最多补8帧")
    if any(not math.isfinite(t) or not 0 <= t < data["duration_s"] for t in targets):
        raise ValueError("补帧时间必须在视频范围内")
    with av.open(str(video)) as container:
        stream = container.streams.video[0]
        stream.codec_context.thread_count = 1
        for i, target in enumerate(targets):
            container.seek(int(target / stream.time_base), stream=stream, backward=True)
            for frame in container.decode(stream):
                if float(frame.time) + 0.001 >= target:
                    filename = f"extra-{used + i:02d}-{target:.3f}s.jpg"
                    image = frame.to_image()
                    image.thumbnail((1280, 1280))
                    image.save(frames_path.parent / filename, quality=88)
                    data["frames"].append(
                        {
                            "requested_s": target,
                            "actual_s": round(float(frame.time), 3),
                            "file": filename,
                            "supplemental": True,
                        }
                    )
                    break
    tmp = frames_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(frames_path)
    return {
        "status": "awaiting_analysis",
        "frames": str(frames_path),
        "supplemental_frames": sum(bool(row.get("supplemental")) for row in data["frames"]),
    }


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        extract(arg)
