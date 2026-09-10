import json
from pathlib import Path

import pytest

from agent_reach.collection import jobs
from agent_reach.collection.get_client import original_fields, valid_douyin_url


def manifest(n=30):
    return {
        "mode": "account",
        "query": "sample",
        "captured_at": "2026-09-10T15:00:00+08:00",
        "discovery_complete": True,
        "end_evidence": "no more",
        "items": [{"video_id": str(10000000000 + i), "pinned": i < 3} for i in range(n)],
    }


def test_selection_boundaries():
    m = manifest(105)
    assert len(jobs.select(m, 30, False)["items"]) == 30
    assert jobs.select(m, 20, False)["items"] == jobs.select(m, 30, False)["items"][:20]
    assert jobs.select(m, None, True)["selected"] == 105
    m["end_evidence"] = ""
    assert not jobs.select(m, None, True)["selection_fulfilled"]
    with pytest.raises(ValueError):
        jobs.select(m, 0, False)


def test_keyword_dates_and_dedup():
    m = manifest(4)
    m["mode"] = "keyword"
    for r, t in zip(
        m["items"],
        [
            "2026-09-09T15:00:00+08:00",
            "2026-09-09T14:59:59+08:00",
            "2026-09-10T15:01:00+08:00",
            "bad",
        ],
    ):
        r["published_at"] = t
    m["items"].insert(1, m["items"][0])
    result = jobs.select(m, None, False)
    assert result["selected"] == 1
    assert len(result["excluded"]) == 3
    assert not result["selection_fulfilled"]


def test_urls_and_original_fields():
    assert valid_douyin_url("https://www.douyin.com/video/7683437126796496174")
    assert valid_douyin_url("https://v.douyin.com/abc/")
    assert not valid_douyin_url("https://www.douyin.com.evil.test/video/7683437126796496174")
    assert original_fields({"summary": "summary"}) == {}
    assert original_fields({"audio": {"original": "speech"}}) == {"audio.original": "speech"}


def fixture_job(tmp_path, owned=True):
    vid = "10000000000"
    folder = tmp_path / vid
    folder.mkdir()
    video = folder / "video.mp4"
    video.write_bytes(b"video")
    text = folder / "original.txt"
    text.write_text("original evidence")
    frames = folder / "frames"
    frames.mkdir()
    (frames / "one.jpg").write_bytes(b"image")
    jobs.save(frames / "frames.json", {"frames": [{"file": "one.jpg", "actual_s": 10}]})
    record = {
        "video_id": vid,
        "source": {"title": "title", "url": "https://www.douyin.com/video/" + vid},
        "status": "awaiting_analysis",
        "video_file": str(video),
        "video_owned": owned,
        "video_sha256": jobs.digest(video),
        "frames": str(frames / "frames.json"),
        "originals": {"web_page.content": {"file": str(text), "sha256": jobs.digest(text)}},
    }
    jobs.save(
        tmp_path / "job.json",
        {"platform": "douyin", "selection_fulfilled": True, "items": [record]},
    )
    review = {k: "observed" for k in ["conclusion", "value", "structure", "visual", "doubts"]}
    review.update(
        video_id=vid,
        quote="evidence",
        original_field="web_page.content",
        identity_verified=True,
        reviewed_frames=["one.jpg"],
    )
    return video, review


def test_review_and_owned_cleanup(tmp_path):
    video, review = fixture_job(tmp_path)
    result = jobs.finalize(tmp_path, review)
    assert result["status"] == "complete"
    assert not video.exists()
    assert Path(result["items"][0]["report"]).exists()
    assert jobs.finalize(tmp_path, review)["status"] == "complete"


def test_external_video_preserved(tmp_path):
    video, review = fixture_job(tmp_path, False)
    jobs.finalize(tmp_path, review)
    assert video.exists()


def test_invalid_review_never_cleans(tmp_path):
    video, review = fixture_job(tmp_path)
    review["quote"] = "not in the original"
    with pytest.raises(ValueError):
        jobs.finalize(tmp_path, review)
    assert video.exists()
    review["quote"] = "evidence"
    review["reviewed_frames"] = ["missing.jpg"]
    with pytest.raises(ValueError):
        jobs.finalize(tmp_path, review)
    assert video.exists()


def test_resume_rejects_other_job(tmp_path):
    m = manifest(1)
    jobs.collect(m, tmp_path, limit=1, use_get=False)
    m["query"] = "different account"
    with pytest.raises(ValueError):
        jobs.collect(m, tmp_path, limit=1, use_get=False)


def test_get_import_rejects_other_video(tmp_path):
    m = manifest(1)
    wrong = tmp_path / "wrong.json"
    wrong.write_text(
        json.dumps(
            {"status": "original_returned", "url": "https://www.douyin.com/video/99999999999"}
        )
    )
    m["items"][0]["get_record"] = str(wrong)
    state = jobs.collect(m, tmp_path / "job", limit=1, use_get=False)
    assert state["items"][0]["status"] == "partial"
    assert "同一视频编号" in state["items"][0]["error"]["message"]


def test_incomplete_discovery_not_claimed_complete(tmp_path):
    _, review = fixture_job(tmp_path)
    path = tmp_path / "job.json"
    state = json.loads(path.read_text())
    state["selection_fulfilled"] = False
    jobs.save(path, state)
    assert jobs.finalize(tmp_path, review)["status"] == "partial"


def test_changed_video_preserved(tmp_path):
    video, review = fixture_job(tmp_path)
    video.write_bytes(b"user replacement")
    with pytest.raises(ValueError, match="替换"):
        jobs.finalize(tmp_path, review)
    assert video.read_bytes() == b"user replacement"


def test_ambiguous_get_submission_not_repeated(tmp_path, monkeypatch):
    from agent_reach.collection import get_client

    vid = "10000000000"
    url = "https://www.douyin.com/video/" + vid
    jobs.save(tmp_path / (vid + ".json"), {"url": url, "status": "submitting"})

    class Client:
        def request(self, *args, **kwargs):
            raise AssertionError("Must not repeat ambiguous creation")

    monkeypatch.setattr(get_client, "Client", Client)
    monkeypatch.setattr("sys.argv", ["get", str(tmp_path), vid, url])
    get_client.main()
    assert json.loads((tmp_path / (vid + ".json")).read_text())["status"] == "submitting"


def test_weread_resume_and_all_scope(tmp_path, monkeypatch):
    import importlib.util

    folder = Path(jobs.__file__).parent / "weread_helper"
    monkeypatch.syspath_prepend(str(folder))
    spec = importlib.util.spec_from_file_location("collection_helper_test", folder / "run.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)

    class Client:
        def __init__(self, *args):
            pass

        def load(self):
            return True

        def validate(self):
            pass

        def renew(self):
            pass

        def account_info(self, account):
            return {"id": account, "name": "sample"}

        def catalog(self, account):
            return [{"id": account + "_article", "title": "title"}]

        def article(self, article):
            return {"text": "real full body"}

    monkeypatch.setattr(helper, "WeReadClient", Client)
    monkeypatch.setattr(helper.time, "sleep", lambda _: None)
    args = {
        "home": str(tmp_path / "auth"),
        "account": "MP_WXS_1234567890",
        "output": str(tmp_path / "job"),
        "all": True,
    }
    result = helper.run(args)
    assert not result["selection_fulfilled"] and not result["discovery_complete"]
    body = Path(result["items"][0]["file"])
    before = body.stat().st_mtime_ns
    review = {
        "article_id": result["items"][0]["id"],
        "quote": "real full body",
        **{k: "observed" for k in ["conclusion", "value", "structure", "doubts"]},
    }
    jobs.finalize(tmp_path / "job", review)
    Client.article = lambda *args: pytest.fail("Already saved body must not be fetched again")
    result = helper.run(args)
    assert result["status"] == "partial" and result["body_saved"] == 1
    assert result["items"][0]["status"] == "complete"
    assert body.stat().st_mtime_ns == before


def test_bounded_supplemental_frames(tmp_path):
    av = pytest.importorskip("av")
    Image = pytest.importorskip("PIL.Image")
    from agent_reach.collection.frames import extract, supplement

    video = tmp_path / "sample.mp4"
    with av.open(str(video), "w") as container:
        stream = container.add_stream("mpeg4", rate=12)
        stream.width = 64
        stream.height = 64
        stream.pix_fmt = "yuv420p"
        for _ in range(12):
            frame = av.VideoFrame.from_image(Image.new("RGB", (64, 64), "blue"))
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    extract(video)
    frames = video.with_suffix("") / "frames.json"
    assert supplement(video, frames, [0.5])["supplemental_frames"] == 1
    with pytest.raises(ValueError, match="8帧"):
        supplement(video, frames, [i / 10 for i in range(8)])
    with pytest.raises(ValueError, match="范围"):
        supplement(video, frames, [100])
