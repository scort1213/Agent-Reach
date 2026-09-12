import json
import subprocess

import pytest

from agent_reach.collection import get_client, twitter, youtube
from agent_reach.collection.jobs import save


def test_x_article_resolution_and_resume_integrity(tmp_path, monkeypatch):
    ident = "2097177704282136838"
    calls = []

    def call(command, value, output):
        calls.append(command)
        rows = ([{"id": ident, "url": f"https://x.com/test/status/{ident}", "text": "https://t.co/abc"}]
                if command == "thread" else
                [{"url": f"https://x.com/test/status/{ident}", "title": "Title", "content": "Full article"}])
        save(output / f"{command}.json", rows)
        return rows

    monkeypatch.setattr(twitter, "call", call)
    state = twitter.collect(ident, tmp_path)
    assert state["content_kind"] == "x_article"
    assert state["status"] == "awaiting_analysis"
    twitter.collect(ident, tmp_path, resume=True)
    assert calls == ["thread", "article"]
    (tmp_path / "body.md").write_text("changed")
    with pytest.raises(ValueError, match="变化"):
        twitter.collect(ident, tmp_path, resume=True)


@pytest.mark.parametrize("url", ["https://youtube.com.evil/watch?v=_Wf0NikMyAA", "file:///tmp/a", "https://www.youtube.com/watch?v=_Wf0NikMyAA&other=1"])
def test_youtube_rejects_ambiguous_input(url):
    with pytest.raises(ValueError):
        youtube.canonical(url)


def test_youtube_get_requires_duration_and_resumes_once(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 1, "", "Caption URL returned empty response"))
    calls = []

    def get(args):
        calls.append(args)
        folder = tmp_path / "get"
        folder.mkdir(exist_ok=True)
        target = folder / "original.txt"
        target.write_text("Actual spoken words")
        save(folder / "_Wf0NikMyAA.json", {"fields": {"web_page.content": {"file": str(target), "sha256": youtube.digest(target)}}})

    monkeypatch.setattr(get_client, "main", get)
    url = "https://www.youtube.com/watch?v=_Wf0NikMyAA"
    with pytest.raises(ValueError, match="时长"):
        youtube.collect(url, tmp_path, use_get=True)
    assert not calls
    meta = tmp_path / "metadata.json"
    save(meta, {"id": "_Wf0NikMyAA", "title": "Sample", "duration": 125})
    assert youtube.collect(url, tmp_path, meta, True)["status"] == "awaiting_analysis"
    youtube.collect(url, tmp_path, meta, True)
    assert len(calls) == 1
    (tmp_path / "get/original.txt").unlink()
    with pytest.raises(OSError):
        youtube.collect(url, tmp_path, meta, True)


def test_youtube_explicit_denial_stops_get(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 1, "", "access_denied"))
    monkeypatch.setattr(get_client, "main", lambda *a: pytest.fail("denied"))
    url = "https://www.youtube.com/watch?v=_Wf0NikMyAA"
    assert youtube.collect(url, tmp_path, use_get=True)["status"] == "blocked"
    assert youtube.collect(url, tmp_path, use_get=True)["status"] == "blocked"


def test_get_youtube_official_payload_and_no_duplicate(tmp_path, monkeypatch):
    calls = []

    class Client:
        def request(self, endpoint, payload=None, *, create=False):
            if create:
                calls.append(payload)
                return {"note_id": "12345"}, {}
            return {"note": {"web_page": {"content": "Spoken content"}}}, {}

    monkeypatch.setattr(get_client, "Client", Client)
    url = "https://www.youtube.com/watch?v=_Wf0NikMyAA"
    args = [str(tmp_path), "_Wf0NikMyAA", url]
    get_client.main(args)
    get_client.main(args)
    assert calls == [{"note_type": "link", "link_url": url}]
    assert json.loads((tmp_path / "_Wf0NikMyAA.json").read_text())["status"] == "original_returned"


def test_x_fresh_failure_replaces_previous_success(tmp_path, monkeypatch):
    ident = "2097177704282136838"
    save(tmp_path / "job.json", {"source_id": ident, "status": "awaiting_analysis"})

    def fail(*args):
        raise ValueError("network failed")

    monkeypatch.setattr(twitter, "call", fail)
    with pytest.raises(ValueError):
        twitter.collect(ident, tmp_path)
    assert json.loads((tmp_path / "job.json").read_text())["status"] == "failed"
    assert twitter.collect(ident, tmp_path, resume=True)["status"] == "failed"


def test_podcast_resume_preserves_budget_and_checks_saved_evidence(tmp_path, monkeypatch):
    from agent_reach.collection import podcast
    from agent_reach.collection.jobs import digest, save

    source = 'https://www.xiaoyuzhoufm.com/episode/' + 'a' * 24
    body = tmp_path / 'original.txt'
    report = tmp_path / 'report.md'
    body.write_text('verified original', encoding='utf-8')
    report.write_text('verified report', encoding='utf-8')
    save(tmp_path / 'job.json', {
        'platform': 'xiaoyuzhou', 'identity': [source, 20], 'budget_seconds': 360,
        'reserved_seconds': 151, 'selection_fulfilled': True, 'status': 'complete',
        'items': [{'status': 'complete', 'episode_id': 'a' * 24,
                   'original_file': str(body), 'original_sha256': digest(body),
                   'report': str(report), 'report_sha256': digest(report)}],
    })
    monkeypatch.setattr(podcast.get_client, 'main', lambda *a: pytest.fail('no duplicate Get submission'))
    state = podcast.collect(source, tmp_path, no_submit=True)
    assert state['status'] == 'complete'
    assert state['budget_seconds'] == 360
    report.unlink()
    state = podcast.collect(source, tmp_path, no_submit=True)
    assert state['status'] == 'awaiting_analysis'
    assert state['items'][0]['reason'] == 'saved_report_unverified_changed_or_missing'
    state['items'][0]['status'] = 'complete'
    save(tmp_path / 'job.json', state)
    body.write_text('modified', encoding='utf-8')
    state = podcast.collect(source, tmp_path, no_submit=True)
    assert state['status'] == 'partial'
    assert state['items'][0]['reason'] == 'saved_original_changed_or_missing'
