import hashlib
import io
import json
from pathlib import Path

import pytest

from agent_reach.collection import get_client, podcast


@pytest.fixture
def windows_default_text_encoding(monkeypatch):
    """Exercise the Windows cp1252 failure on any host without a global flag."""
    original_open = Path.open

    def cp1252_open(self, mode="r", buffering=-1, encoding=None, errors=None, newline=None):
        if "b" not in mode and encoding in (None, "locale"):
            encoding = "cp1252"
        return original_open(self, mode, buffering, encoding, errors, newline)

    monkeypatch.setattr(Path, "open", cp1252_open)


def test_get_chinese_roundtrip_and_hash_with_non_utf8_defaults(
    tmp_path, monkeypatch, windows_default_text_encoding,
):
    original = "第一段中文转写。\n第二段内容与数字123。\n" * 20
    creations = []

    class Client:
        def request(self, endpoint, payload=None, *, create=False):
            if create:
                creations.append(payload)
                return {"note_id": "12345"}, {}
            return {"note": {"title": "测试中文标题", "audio": {"original": original}}}, {}

    monkeypatch.setattr(get_client, "Client", Client)
    output = io.BytesIO()
    console = io.TextIOWrapper(output, encoding="cp1252")
    monkeypatch.setattr("sys.stdout", console)
    args = [str(tmp_path), "12345678901", "https://v.douyin.com/fixture/"]
    assert get_client.main(args) == get_client.main(args) == 0
    assert len(creations) == 1
    record = json.loads((tmp_path / "12345678901.json").read_text(encoding="utf-8"))
    saved = Path(record["fields"]["audio.original"]["file"])
    assert saved.read_bytes() == original.encode("utf-8")
    assert record["fields"]["audio.original"]["sha256"] == hashlib.sha256(saved.read_bytes()).hexdigest()
    console.flush()
    assert output.getvalue().isascii(), "JSON console output must remain codepage independent"


def test_podcast_chinese_failure_record_with_non_utf8_defaults(
    tmp_path, monkeypatch, windows_default_text_encoding,
):
    class Client:
        def request(self, *args, **kwargs):
            return {"notes": [{"title": "同名单集", "note_type": "local_audio", "note_id": str(n)}
                              for n in (1, 2)]}, {}

    monkeypatch.setattr(get_client, "Client", Client)
    result = podcast.collect("https://www.xiaoyuzhoufm.com/episode/" + "a" * 24,
                             tmp_path, audio_note_title="同名单集")
    assert result["status"] == "blocked"
    saved = json.loads((tmp_path / "last-error.json").read_text(encoding="utf-8"))
    assert "未唯一定位" in saved["error"]["message"]
