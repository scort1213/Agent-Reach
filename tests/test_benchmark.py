import json
import sys
from pathlib import Path

import pytest

from agent_reach.benchmark import review, route_assessments, run_case, run_cases, scrub, summary


def case(**kwargs):
    return {'id': 'sample', 'platform': 'test', 'stage': 'content', 'task': 'Read actual content',
            'argv': [sys.executable, '-c', 'print("Title: sample article. Body: actual source evidence.")'],
            'cache': 'fresh_request', **kwargs}


def test_transport_success_is_not_content_success(tmp_path):
    p = run_case(case(), tmp_path, 'r1')
    d = json.loads(p.read_text())
    assert d['transport'] == 'returned'
    assert d['assessment'] == 'unreviewed'
    assert summary(tmp_path)['platforms']['test']['passed'] == 0


def test_review_requires_actual_distinct_evidence(tmp_path):
    p = run_case(case(), tmp_path, 'r1')
    for quotes in [['fabricated quote', 'another false quote'], ['Title: sample', 'Title: sample']]:
        with pytest.raises(ValueError):
            review(p, {'verdict': 'pass', 'reason': 'read', 'quotes': quotes})
    assert review(p, {'verdict': 'pass', 'reason': 'Read title and body',
                     'quotes': ['Title: sample', 'Body: actual source evidence']})['assessment'] == 'pass'


def test_tampered_evidence_cannot_be_reviewed(tmp_path):
    p = run_case(case(), tmp_path, 'r1')
    Path(json.loads(p.read_text())['evidence']).write_text('changed')
    with pytest.raises(ValueError, match='changed'):
        review(p, {'verdict': 'fail', 'reason': 'changed'})


def test_analysis_needs_substantive_attestation(tmp_path):
    p = run_case(case(stage='analysis'), tmp_path, 'r1')
    with pytest.raises(ValueError, match='Analysis needs'):
        review(p, {'verdict': 'pass', 'reason': 'read',
                   'quotes': ['Title: sample', 'Body: actual source evidence']})


def test_failed_command_cannot_pass(tmp_path):
    p = run_case(case(argv=[sys.executable, '-c', 'print("Title: sample Body: actual source evidence");exit(1)']), tmp_path, 'r1')
    with pytest.raises(ValueError, match='Failed command'):
        review(p, {'verdict': 'pass', 'reason': 'read',
                   'quotes': ['Title: sample', 'Body: actual source evidence']})


def test_blocked_case_does_not_execute(tmp_path):
    p = run_case(case(blocked_reason='tool denied', argv=['nonexistent-command']), tmp_path, 'r1')
    assert json.loads(p.read_text())['transport'] == 'not_attempted'


def test_explicit_denial_stops_same_platform_only(tmp_path):
    cases = [case(argv=[sys.executable, '-c', 'print("Navigation rejected");exit(1)']),
             case(id='dependent'), case(id='independent', platform='other')]
    results = [json.loads(p.read_text()) for p in run_cases(cases, tmp_path, 'r1')]
    assert [r['transport'] for r in results] == ['command_failed', 'not_attempted', 'returned']


def test_timeout_and_missing_dependency(tmp_path):
    p = run_case(case(timeout=0.05, argv=[sys.executable, '-c', 'import time;time.sleep(5)']), tmp_path, 'r1')
    assert json.loads(p.read_text())['transport'] == 'timeout'
    p = run_case(case(argv=['no-such-benchmark-command-xyz']), tmp_path, 'r1')
    assert json.loads(p.read_text())['transport'] == 'missing_dependency'


def test_scrubbing():
    value = scrub('gk_live_dummy.secret\nAuthorization: Bearer secret\napi_key=secret\nCookie: private\npublic body')
    assert 'secret' not in value and 'private' not in value
    assert 'public body' in value


def test_cached_import_is_not_fresh_request(tmp_path):
    evidence = tmp_path / 'source.txt'
    evidence.write_text('Title: sample article. Body: actual source evidence.')
    p = run_case(case(evidence_file=str(evidence), cache='cached_replay'), tmp_path, 'r1')
    review(p, {'verdict': 'pass', 'reason': 'Replay only',
               'quotes': ['Title: sample', 'Body: actual source evidence']})
    row = summary(tmp_path)['platforms']['test']['cases']['sample']['rounds']['r1'][0]
    assert row['cache'] == 'cached_replay' and row['transport'] == 'imported'


def test_identifier_cannot_escape_output(tmp_path):
    with pytest.raises(ValueError):
        run_case(case(id='../escape'), tmp_path, 'r1')


def test_secret_arguments_rejected_before_execution(tmp_path):
    with pytest.raises(ValueError, match='credential storage'):
        run_case(case(argv=['some-command', '--api-key', 'secret']), tmp_path, 'r1')


def test_source_probe_preserves_http_failure(monkeypatch, capsys):
    import requests

    from agent_reach import benchmark_sources

    class Failure:
        status_code = 403
        url = 'https://example.invalid/article'
        text = 'access denied'

        def raise_for_status(self):
            raise requests.HTTPError('403')

    monkeypatch.setattr(sys, 'argv', ['probe', 'http', Failure.url])
    monkeypatch.setattr(benchmark_sources.requests, 'get', lambda *a, **kw: Failure())
    with pytest.raises(requests.HTTPError):
        benchmark_sources.main()
    assert json.loads(capsys.readouterr().out)['http'] == 403


def test_source_probe_limits_feed_without_claiming_fulltext(monkeypatch, capsys):
    from types import SimpleNamespace

    from agent_reach import benchmark_sources

    response = SimpleNamespace(content=b'<rss/>', raise_for_status=lambda: None)
    feed = SimpleNamespace(feed={'title': 'Test'}, entries=[{'title': str(i), 'link': f'https://example.invalid/{i}', 'summary': 'only excerpt'} for i in range(10)])
    monkeypatch.setattr(sys, 'argv', ['probe', 'rss', 'https://example.invalid/feed'])
    monkeypatch.setattr(benchmark_sources.requests, 'get', lambda *a, **kw: response)
    monkeypatch.setattr(benchmark_sources.feedparser, 'parse', lambda *a: feed)
    benchmark_sources.main()
    data = json.loads(capsys.readouterr().out)
    assert len(data['items']) == 3
    assert 'body' not in data['items'][0]


def test_summary_keeps_missing_routes_and_versions_separate(tmp_path):
    (tmp_path / 'run.json').write_text(json.dumps({'planned_routes': [{'platform': 'test'}, {'platform': 'missing'}]}))
    run_case(case(candidate_version='v1', backend='formal'), tmp_path, 'r1')
    run_case(case(candidate_version='v2', backend='optional', cache='cached_replay'), tmp_path, 'r2')
    coverage = summary(tmp_path)['planned_coverage']
    assert len(coverage) == 2
    assert not coverage['missing']['recorded']
    assert coverage['test']['versions'] == ['v1', 'v2']
    assert coverage['test']['backends'] == ['formal', 'optional']
    assert coverage['test']['cached_replays'] == 1
    assert coverage['test']['unreviewed'] == 2


def test_route_requires_correct_backend_all_stages_identity_and_rounds(tmp_path):
    planned = {'candidate_version': 'v1', 'planned_routes': [{
        'platform': 'test', 'route_group': 'custom',
        'formal_backends': {s: ['formal'] for s in ('discovery', 'content', 'analysis')},
        'fallback_backends': {s: ['backup'] for s in ('discovery', 'content', 'analysis')},
    }]}
    rows = []
    for rnd in ('r1', 'r2', 'r3'):
        for stage in ('discovery', 'content', 'analysis'):
            p = run_case(case(stage=stage, backend='formal', candidate_version='v1', source_ids=['a']), tmp_path, rnd)
            rows.append(review(p, {'verdict': 'pass', 'reason': 'Reviewed evidence',
                                  'quotes': ['Title: sample', 'Body: actual source evidence'],
                                  'conclusion': 'sample', 'value': 'sample', 'limitations': 'sample'}))
    def verdict():
        return route_assessments(rows, planned)['routes']['test']['verdict']
    assert verdict() == 'pass'
    rows[-1]['backend'] = 'optional'
    assert verdict() == 'partial'
    rows[-1]['backend'] = 'backup'
    assert verdict() == 'fallback_pass'
    rows[-1]['source_ids'] = ['different-article']
    assert verdict() == 'partial'
    rows[-1]['source_ids'] = ['a']
    rows[-1]['cache'] = 'cached_replay'
    assert verdict() == 'partial'
    rows[-1]['cache'] = 'fresh_request'
    rows[-1]['candidate_version'] = 'v0'
    assert verdict() == 'partial'
    rows[-1]['candidate_version'] = 'v1'
    Path(rows[-1]['evidence']).unlink()
    assert verdict() == 'partial'
