import json

import pytest

from agent_reach.collection import get_client, podcast

EID = 'a' * 24
SOURCE = f'https://www.xiaoyuzhoufm.com/episode/{EID}'


def sample():
    return {'episode_id': EID, 'url': SOURCE, 'title': 'fixture', 'shownotes': 'description',
            'duration_s': 60, 'media_url': 'https://media.xyzcdn.net/test.m4a', 'status': 'discovered'}


def test_resumption_does_not_reserve_twice(tmp_path, monkeypatch):
    monkeypatch.setattr(podcast, 'discover', lambda *a: ([sample()], True, 1))
    calls = []

    def get(args):
        calls.append(args)
        p = tmp_path / f'{EID}.json'
        if not p.exists():
            get_client.save(p, {'status': 'pending_timeout', 'task_id': 'fixture'})

    monkeypatch.setattr(get_client, 'main', get)
    a = podcast.collect(SOURCE, tmp_path)
    b = podcast.collect(SOURCE, tmp_path)
    assert a['reserved_seconds'] == b['reserved_seconds'] == 60
    assert b['items'][0]['status'] == 'needs_review'
    assert len(calls) == 2  # Client resumes the existing task, not a new label.


def test_budget_prevents_creation(tmp_path, monkeypatch):
    monkeypatch.setattr(podcast, 'discover', lambda *a: ([sample()], True, 1))
    monkeypatch.setattr(get_client, 'main', lambda *a: pytest.fail('over budget'))
    result = podcast.collect(SOURCE, tmp_path, max_minutes=0.5)
    assert result['items'][0]['status'] == 'budget_exceeded'


def test_shownotes_alone_cannot_complete(tmp_path, monkeypatch):
    monkeypatch.setattr(podcast, 'discover', lambda *a: ([sample()], True, 1))
    podcast.collect(SOURCE, tmp_path, no_submit=True)
    with pytest.raises(ValueError, match='尚无待验收原文'):
        podcast.review(tmp_path, {'episode_id': EID})


def test_private_media_not_extracted():
    x = podcast.episode({'eid': EID, 'isPrivateMedia': True,
                         'media': {'source': {'mode': 'PUBLIC', 'url': 'https://media.xyzcdn.net/a'}}})
    assert x['media_url'] is None and x['status'] == 'access_unavailable'


def test_first_page_is_not_all(monkeypatch):
    monkeypatch.setattr(podcast, 'fetch', lambda u: {'podcast': {'pid': EID, 'episodeCount': 269,
                                                                  'episodes': [{'eid': EID}]}})
    items, complete, total = podcast.discover(SOURCE.replace('/episode/', '/podcast/'), 20)
    assert len(items) == 1 and total == 269 and not complete


def test_get_unknown_submission_is_not_repeated(tmp_path, monkeypatch):
    get_client.save(tmp_path / 'fixture.json', {'url': SOURCE, 'status': 'submitting'})
    class Client:
        def request(self, *a, **k):
            pytest.fail('unknown create outcome must not repeat')
    monkeypatch.setattr(get_client, 'Client', Client)
    assert get_client.main([str(tmp_path), 'fixture', SOURCE]) == 2


def test_read_only_metadata_does_not_load_get_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(podcast, 'discover', lambda *a: ([sample()], True, 1))
    monkeypatch.setattr(get_client, 'Client', lambda: pytest.fail('no credentials for discovery'))
    podcast.collect(SOURCE, tmp_path, no_submit=True)
    assert json.loads((tmp_path / 'job.json').read_text(encoding="utf-8"))['reserved_seconds'] == 0


def test_foreign_output_is_preserved(tmp_path):
    path = tmp_path / 'job.json'
    path.write_text('{"identity": ["another", 2]}')
    before = path.read_bytes()
    with pytest.raises(ValueError, match='另一任务'):
        podcast.collect(SOURCE, tmp_path)
    assert path.read_bytes() == before


def test_nan_budget_rejected_before_discovery(tmp_path, monkeypatch):
    monkeypatch.setattr(podcast, 'discover', lambda *a: pytest.fail('invalid budget'))
    with pytest.raises(ValueError):
        podcast.collect(SOURCE, tmp_path, max_minutes=float('nan'))


def test_duplicate_get_titles_do_not_pick_one(tmp_path, monkeypatch):
    class Client:
        def request(self, *a, **k):
            return {'notes': [{'title': 'same', 'note_type': 'local_audio', 'note_id': n} for n in ['1', '2']]}, {}
    monkeypatch.setattr(get_client, 'Client', Client)
    state = podcast.collect(SOURCE, tmp_path, audio_note_title='same')
    assert state['status'] == 'blocked'


def test_doctor_get_route_does_not_trigger_transcription(monkeypatch):
    from agent_reach.channels import xiaoyuzhou
    monkeypatch.setattr(xiaoyuzhou, 'get_configured', lambda: True)
    monkeypatch.setattr(get_client, 'Client', lambda: pytest.fail('doctor cannot submit'))
    ch = xiaoyuzhou.XiaoyuzhouChannel()
    status, message = ch.check()
    assert status == 'warn' and 'collect-podcast' in message
    assert ch.active_backend is None
