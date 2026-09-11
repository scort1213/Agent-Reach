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


def accepted_rows(tmp_path, *, strict=False, platform='test'):
    """Small real evidence files, with independent observation IDs for each round."""
    import hashlib

    planned = {'candidate_version': 'v1', 'acceptance_schema': 2 if strict else 1, 'planned_routes': [{
        'platform': platform, 'route_group': 'custom',
        'formal_backends': {s: ['formal'] for s in ('discovery', 'content', 'analysis')},
        'fallback_backends': {s: ['backup'] for s in ('discovery', 'content', 'analysis')},
    }]}
    evidence = tmp_path / 'source.txt'
    evidence.write_text('Title: sample article. Body: actual source evidence.')
    artifacts = []
    for kind in ('body', 'transcript', 'frame', 'report'):
        file = tmp_path / (kind + '.txt')
        file.write_text('Title: sample article. Body: actual source evidence.')
        artifacts.append({'path': str(file), 'sha256': hashlib.sha256(file.read_bytes()).hexdigest(),
                          'kind': kind, 'source_id': 'a'})
    rows = []
    for index, rnd in enumerate(('r1', 'r2', 'r3')):
        for stage in ('discovery', 'content', 'analysis'):
            previous = next((r for r in rows if r['round'] == 'r2' and r['stage'] == stage), {})
            p = run_case(case(platform=platform, stage=stage, backend='formal', actual_entry='formal',
                              candidate_version='v1', source_ids=['a'], evidence_file=str(evidence),
                              execution_id='process-' + rnd, round_mode=('initial', 'repeat', 'resume')[index],
                              resume_from=previous.get('observation_id'), artifacts=artifacts,
                              cache='resume_validation' if index == 2 else 'fresh_request'), tmp_path, rnd)
            rows.append(review(p, {'verdict': 'pass', 'reason': 'Reviewed actual evidence',
                                  'quotes': ['Title: sample', 'Body: actual source evidence'],
                                  'conclusion': 'sample', 'value': 'sample', 'limitations': 'sample',
                                  'content_complete': True, 'visual_checked': True}))
    return planned, rows


def route_verdict(planned, rows):
    return next(iter(route_assessments(rows, planned)['routes'].values()))['verdict']


def test_latest_failure_cannot_be_hidden_by_old_pass(tmp_path):
    planned, rows = accepted_rows(tmp_path)
    p = run_case(case(stage='content', backend='formal', candidate_version='v1', source_ids=['a']), tmp_path, 'r3')
    failure = review(p, {'verdict': 'fail', 'reason': 'Body unavailable', 'failure_kind': 'incomplete_content'})
    rows.append(failure)
    assert route_verdict(planned, rows) == 'partial'
    check = route_assessments(rows, planned)['routes']['test']['checks'][-1]
    assert failure['observation_id'] in check['effective_attempts']['content']
    assert 'content' in check['missing']


def test_unreviewed_retry_supersedes_old_pass(tmp_path):
    planned, rows = accepted_rows(tmp_path)
    p = run_case(case(stage='content', backend='formal', candidate_version='v1', source_ids=['a']), tmp_path, 'r3')
    rows.append(json.loads(p.read_text()))
    assert route_verdict(planned, rows) == 'partial'


def test_strict_rounds_require_distinct_resume_process_and_reference(tmp_path):
    planned, rows = accepted_rows(tmp_path, strict=True)
    assert route_verdict(planned, rows) == 'pass'
    rows[-1]['execution_id'] = 'process-r2'
    assert route_verdict(planned, rows) == 'partial'
    rows[-1]['execution_id'] = 'process-r3'
    rows[-1]['resume_from'] = 'not-an-observation'
    assert route_verdict(planned, rows) == 'partial'


def test_changing_round_labels_does_not_create_new_observations(tmp_path):
    planned, rows = accepted_rows(tmp_path, strict=True)
    rows[-1]['observation_id'] = rows[2]['observation_id']
    assert route_verdict(planned, rows) == 'partial'


def test_strict_manifest_does_not_upgrade_legacy_records(tmp_path):
    planned, rows = accepted_rows(tmp_path, strict=True)
    for row in rows:
        row.pop('record_schema')
    assert route_verdict(planned, rows) == 'partial'


@pytest.mark.parametrize('cache', ['cached_replay', 'unknown', 'resume_validation'])
def test_first_round_needs_new_observation_not_replay_or_resume(tmp_path, cache):
    planned, rows = accepted_rows(tmp_path, strict=True)
    rows[0]['cache'] = cache
    assert route_verdict(planned, rows) == 'partial'


def test_valid_empty_result_never_counts_as_content_analysis(tmp_path):
    planned, rows = accepted_rows(tmp_path, strict=True)
    rows[0]['assessment'] = 'empty_valid'
    result = route_assessments(rows, planned)['routes']['test']
    assert result['verdict'] == 'partial'
    assert result['empty_results'] == 1


@pytest.mark.parametrize('kind', ['body', 'report'])
def test_missing_or_modified_saved_artifacts_invalidate_completion(tmp_path, kind):
    planned, rows = accepted_rows(tmp_path, strict=True)
    file = tmp_path / (kind + '.txt')
    file.write_text('truncated data')
    assert route_verdict(planned, rows) == 'partial'
    file.unlink()
    assert route_verdict(planned, rows) == 'partial'


def test_truncated_body_cannot_count_as_complete(tmp_path):
    planned, rows = accepted_rows(tmp_path, strict=True)
    rows[1]['review']['content_complete'] = False
    assert route_verdict(planned, rows) == 'partial'


def test_video_requires_matching_frames_and_actual_visual_review(tmp_path):
    planned, rows = accepted_rows(tmp_path, strict=True, platform='douyin')
    assert route_verdict(planned, rows) == 'pass'
    rows[-1]['review']['visual_checked'] = False
    assert route_verdict(planned, rows) == 'partial'
    rows[-1]['review']['visual_checked'] = True
    rows[-1]['artifacts'] = [a for a in rows[-1]['artifacts'] if a['kind'] != 'frame']
    assert route_verdict(planned, rows) == 'partial'


def test_artifact_for_other_source_cannot_fill_missing_video(tmp_path):
    planned, rows = accepted_rows(tmp_path, strict=True, platform='douyin')
    rows[-1]['artifacts'] = [dict(a, source_id='different-video') if a['kind'] == 'frame' else a
                             for a in rows[-1]['artifacts']]
    assert route_verdict(planned, rows) == 'partial'


def test_four_browser_entries_preserved_and_initialization_not_platform_denial(tmp_path):
    cases = [case(id='chrome-failed', backend='agent_computer_use', actual_entry='chrome_browser',
                  failure_kind='initialization', argv=[sys.executable, '-c', 'exit(1)'])]
    cases += [case(id=entry, backend='agent_computer_use', actual_entry=entry)
              for entry in ('cua_native', 'iab', 'opencli')]
    rows = [json.loads(p.read_text()) for p in run_cases(cases, tmp_path, 'r1')]
    assert [r['actual_entry'] for r in rows] == ['chrome_browser', 'cua_native', 'iab', 'opencli']
    assert [r['transport'] for r in rows] == ['command_failed', 'returned', 'returned', 'returned']
    assert rows[0]['failure_kind'] == 'initialization'


def test_structured_target_denial_stops_same_target_across_entries(tmp_path):
    cases = [case(id='denied', actual_entry='chrome_browser', failure_kind='access_denied',
                  failure_scope='https://example.test/one', source_urls=['https://example.test/one']),
             case(id='same', actual_entry='cua_native', source_urls=['https://example.test/one']),
             case(id='unrelated', actual_entry='cua_native', source_urls=['https://example.test/two'])]
    rows = [json.loads(p.read_text()) for p in run_cases(cases, tmp_path, 'r1')]
    assert rows[1]['transport'] == 'not_attempted'
    assert rows[1]['failure_kind'] == 'access_denied'
    assert rows[2]['transport'] == 'returned'


def test_review_cannot_reclassify_explicit_denial_as_initialization(tmp_path):
    p = run_case(case(argv=[sys.executable, '-c', 'print("Navigation rejected")']), tmp_path, 'r1')
    with pytest.raises(ValueError, match='cannot be reclassified'):
        review(p, {'verdict': 'blocked', 'reason': 'incorrect category', 'failure_kind': 'initialization'})


def test_denial_cannot_be_bypassed_by_different_formal_entry(tmp_path):
    planned, rows = accepted_rows(tmp_path, strict=True)
    p = run_case(case(stage='discovery', backend='formal', actual_entry='chrome_browser',
                      candidate_version='v1', failure_kind='access_denied'), tmp_path, 'r1')
    rows.insert(0, review(p, {'verdict': 'blocked', 'reason': 'Tool explicitly denied this platform'}))
    assert route_verdict(planned, rows) == 'partial'


def test_unknown_failure_does_not_authorize_fallback(tmp_path):
    planned, rows = accepted_rows(tmp_path)
    p = run_case(case(stage='content', backend='formal', candidate_version='v1', source_ids=['a']), tmp_path, 'r3')
    rows.append(review(p, {'verdict': 'fail', 'reason': 'Unclassified error'}))
    rows[-3]['backend'] = 'backup'  # r3 content, otherwise eligible legacy fallback.
    assert route_verdict(planned, rows) == 'partial'


def test_fallback_success_preserves_primary_failure_and_recovery_link(tmp_path):
    planned, rows = accepted_rows(tmp_path, strict=True)
    p = run_case(case(stage='content', backend='formal', actual_entry='formal', candidate_version='v1', source_ids=['a']), tmp_path, 'r3')
    failure = review(p, {'verdict': 'fail', 'reason': 'Known missing CLI dependency', 'failure_kind': 'missing_dependency'})
    rows.append(failure)
    old = rows[7]
    p = run_case(case(stage='content', backend='backup', actual_entry='backup', candidate_version='v1',
                      source_ids=['a'], execution_id='fallback-process', round_mode='resume',
                      resume_from=rows[4]['observation_id'], cache='resume_validation',
                      artifacts=old['artifacts'], recovery_of=[failure['observation_id']]), tmp_path, 'r3')
    rows.append(review(p, old['review']))
    result = route_assessments(rows, planned)['routes']['test']
    assert result['verdict'] == 'fallback_pass'
    assert result['primary_failures'] == 1
    assert result['failures'][0]['failure_kind'] == 'missing_dependency'
    rows[-1]['recovery_of'] = []
    assert route_verdict(planned, rows) == 'partial'


def test_tied_timestamp_failure_wins_over_pass(tmp_path):
    planned, rows = accepted_rows(tmp_path)
    failure = dict(rows[-1], assessment='fail', failure_kind='incomplete_content', observation_id='0')
    rows.append(failure)
    assert route_verdict(planned, rows) == 'partial'


def test_entry_local_initialization_does_not_invalidate_native_task_success(tmp_path):
    planned, rows = accepted_rows(tmp_path, strict=True)
    p = run_case(case(stage='discovery', backend='formal', actual_entry='chrome_browser',
                      candidate_version='v1', failure_kind='initialization'), tmp_path, 'r1')
    rows.append(review(p, {'verdict': 'fail', 'reason': 'Chrome policy initialization failed'}))
    assert route_verdict(planned, rows) == 'pass'
    assert route_assessments(rows, planned)['routes']['test']['primary_failures'] == 1


def test_unknown_entry_failure_is_not_automatic_permission_to_change_entry(tmp_path):
    planned, rows = accepted_rows(tmp_path, strict=True)
    failure = dict(rows[0], actual_entry='chrome_browser', assessment='fail',
                   failure_kind='unknown', observation_id='unknown', started_at='2000-01-01')
    rows.insert(0, failure)
    assert route_verdict(planned, rows) == 'partial'


def test_explicit_denial_needs_separate_normal_restoration_evidence(tmp_path):
    planned, rows = accepted_rows(tmp_path, strict=True)
    failure = dict(rows[0], assessment='blocked', failure_kind='access_denied',
                   observation_id='denial', started_at='2000-01-01')
    for row in rows:
        row['recovery_of'] = ['denial']
        row['review']['recovery'] = {'kind': 'normal_access_restored', 'reason': 'Normal tool access restored',
                                     'quotes': ['Title: sample', 'Body: actual source evidence']}
    rows.insert(0, failure)
    assert route_verdict(planned, rows) == 'pass'
    rows[-1]['review']['recovery']['quotes'] = ['invented evidence', 'another invented quote']
    assert route_verdict(planned, rows) == 'partial'


def test_denial_survives_another_command_batch_in_same_run(tmp_path):
    list(run_cases([case(failure_kind='access_denied', failure_scope='test')], tmp_path, 'r1'))
    later = [json.loads(p.read_text()) for p in run_cases([case(id='later', actual_entry='cua_native')], tmp_path, 'r2')]
    assert later[0]['transport'] == 'not_attempted'
    assert later[0]['failure_kind'] == 'access_denied'


def test_restoration_observation_can_be_imported_but_must_be_reviewed(tmp_path):
    denial_path = list(run_cases([case(failure_kind='access_denied', failure_scope='test')], tmp_path, 'r1'))[0]
    denial = json.loads(denial_path.read_text())
    source = tmp_path / 'normal-access.txt'
    source.write_text('Title: sample article. Body: actual source evidence.')
    restoration_path = list(run_cases([case(id='restored', evidence_file=str(source),
                                            recovery_of=[denial['observation_id']])], tmp_path, 'r2'))[0]
    assert json.loads(restoration_path.read_text())['transport'] == 'imported'
    review(restoration_path, {'verdict': 'pass', 'reason': 'Normal tool access was restored',
                              'quotes': ['Title: sample', 'Body: actual source evidence'],
                              'recovery': {'kind': 'normal_access_restored', 'reason': 'Observed successful normal access',
                                           'quotes': ['Title: sample', 'Body: actual source evidence']}})
    later = json.loads(list(run_cases([case(id='later')], tmp_path, 'r3'))[0].read_text())
    assert later['transport'] == 'returned'


def test_report_mentioning_past_denial_is_not_a_new_tool_denial(tmp_path):
    p = run_case(case(failure_kind='initialization',
                      argv=[sys.executable, '-c', 'print("Previous Navigation rejected error is unrelated")']), tmp_path, 'r1')
    assert json.loads(p.read_text())['failure_kind'] == 'initialization'


def test_structured_tool_error_is_an_explicit_denial(tmp_path):
    source = tmp_path / 'tool-error.json'
    source.write_text(json.dumps({'isError': True, 'content': [{'text': 'Error: Navigation rejected for this target'}]}))
    p = run_case(case(evidence_file=str(source)), tmp_path, 'r1')
    assert json.loads(p.read_text())['failure_kind'] == 'access_denied'


def test_resume_may_use_first_valid_saved_round_not_only_second_round(tmp_path):
    planned, rows = accepted_rows(tmp_path, strict=True)
    for offset in range(3):
        rows[6 + offset]['resume_from'] = rows[offset]['observation_id']
    assert route_verdict(planned, rows) == 'pass'


@pytest.mark.parametrize('invalid_reference', ['self', 'future', 'other_run', 'historical_replay'])
def test_resume_reference_must_be_prior_live_observation_in_this_run(tmp_path, invalid_reference):
    planned, rows = accepted_rows(tmp_path, strict=True)
    if invalid_reference == 'self':
        rows[-1]['resume_from'] = rows[-1]['observation_id']
    elif invalid_reference == 'future':
        rows[5]['round'] = 'r4'
    elif invalid_reference == 'other_run':
        rows[5]['run_root'] = '/another/benchmark/run'
    else:
        rows[5]['cache'] = 'cached_replay'
    assert route_verdict(planned, rows) == 'partial'


def test_primary_connection_failure_is_counted_with_successful_fallback(tmp_path):
    planned, rows = accepted_rows(tmp_path)
    for row in rows:
        row['backend'] = 'backup'
    p = run_case(case(stage='connection', backend='formal', candidate_version='v1'), tmp_path, 'preflight')
    rows.append(review(p, {'verdict': 'blocked', 'reason': 'Not logged in', 'failure_kind': 'login_required'}))
    result = route_assessments(rows, planned)['routes']['test']
    assert result['verdict'] == 'fallback_pass'
    assert result['primary_failures'] == 1
    assert result['failures'][0]['stage'] == 'connection'


def test_opencli_yaml_denial_is_structured_and_blocks_dependent_commands(tmp_path):
    error = "ok: false\nerror:\n  code: COMMAND_EXEC\n  message: 'Pre-navigation to https://example.test failed: Navigation rejected.'\n  exitCode: 1\n  cause: Navigation rejected.\n"
    rows = [json.loads(p.read_text()) for p in run_cases([
        case(argv=[sys.executable, '-c', 'import sys;sys.stderr.write(' + repr(error) + ');sys.exit(1)']),
        case(id='dependent', actual_entry='cua_native')], tmp_path, 'r1')]
    assert rows[0]['failure_kind'] == 'access_denied'
    assert rows[1]['transport'] == 'not_attempted'


def test_attempt_ordering_compares_instants_across_timezones(tmp_path):
    planned, rows = accepted_rows(tmp_path)
    rows[-1]['started_at'] = '2026-09-11T15:30:00Z'
    failure = dict(rows[-1], assessment='fail', failure_kind='adapter_error', observation_id='offset-failure',
                   started_at='2026-09-11T23:00:00+08:00')
    rows.append(failure)
    assert route_verdict(planned, rows) == 'pass'
    failure['started_at'] = '2026-09-11T23:31:00+08:00'
    assert route_verdict(planned, rows) == 'partial'


@pytest.mark.parametrize('url,blocked', [
    ('https://douyin.com/video/123', True),
    ('https://www.douyin.com/video/123', True),
    ('https://live.douyin.com/video/123', True),
    ('https://evil-douyin.com/video/123', False),
    ('https://douyin.com.evil.example/video/123', False),
])
def test_hostname_denial_matches_domain_boundary(tmp_path, url, blocked):
    list(run_cases([case(failure_kind='access_denied', failure_scope='douyin.com')], tmp_path, 'r1'))
    row = json.loads(list(run_cases([case(id='next', actual_entry='cua_native', source_urls=[url])], tmp_path, 'r2'))[0].read_text())
    assert (row['transport'] == 'not_attempted') == blocked


def test_exact_url_denial_keeps_target_scope_and_ignores_fragment(tmp_path):
    url = 'https://www.example.test/article/123?id=one'
    list(run_cases([case(failure_kind='access_denied', failure_scope=url)], tmp_path, 'r1'))
    cases = [case(id='same', source_urls=[url+'#end']),
             case(id='other-path', source_urls=['https://www.example.test/article/124?id=one']),
             case(id='other-query', source_urls=['https://www.example.test/article/123?id=two'])]
    rows = [json.loads(p.read_text()) for p in run_cases(cases, tmp_path, 'r2')]
    assert [r['transport'] for r in rows] == ['not_attempted','returned','returned']


def test_denied_host_with_only_opaque_source_id_remains_blocked(tmp_path):
    list(run_cases([case(failure_kind='access_denied', failure_scope='douyin.com')], tmp_path, 'r1'))
    row = json.loads(list(run_cases([case(id='next', source_ids=['123'])], tmp_path, 'r2'))[0].read_text())
    assert row['transport'] == 'not_attempted'
