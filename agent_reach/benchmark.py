"""Evidence-first benchmark harness. Exit status is never a content verdict.

python -m agent_reach.benchmark run cases.json --output DIR --round baseline
python -m agent_reach.benchmark review result.json review.json
python -m agent_reach.benchmark summary --output DIR

Case files contain trusted operator-authored argv arrays, never website instructions.
Browser steps are executed by the calling Agent and imported as local evidence.
"""

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

FAILURE_KINDS = {
    'initialization', 'login_required', 'adapter_error', 'incomplete_content',
    'access_denied', 'timeout', 'missing_dependency', 'unknown',
}


def _intact(path, digest):
    try:
        return bool(digest) and hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
    except (OSError, TypeError):
        return False


def _failure_metadata(data):
    if data.get('failure_kind') not in FAILURE_KINDS | {None}:
        raise ValueError('Invalid failure_kind')
    return {key: data[key] for key in ('failure_kind', 'failure_stage', 'failure_scope') if data.get(key) is not None}


def scrub(value):
    text = str(value)
    text = re.sub(r"gk_live_[\w.-]+|gh[pousr]_[\w]+", "[REDACTED]", text)
    text = re.sub(r"(?i)(authorization|cookie|api[_-]?key|access[_-]?token)(\s*[=:]\s*)[^\n]+", r"\1\2[REDACTED]", text)
    return text


def safe_argv(argv):
    """Reject secrets in command flags before executing or recording them."""
    for item in argv:
        if re.match(r'(?i)^--?(api[-_]key|token|password|cookie|authorization)(=|$)', item):
            raise ValueError('Use existing credential storage, never command-line secret flags')
        if re.search(r'gk_live_[\w.-]+|gh[pousr]_[\w]+', item):
            raise ValueError('Credential detected in command argument')
    return argv


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    temp.replace(path)


def _explicit_denial(stdout, stderr):
    """Recognize a tool error, not a page or report merely mentioning that error."""
    for text in (stdout, stderr):
        if re.search(r'^\s*(?:Error:\s*)?Navigation rejected(?:[\s:.!]|$)', text, re.MULTILINE):
            return True
        # OpenCLI emits structured YAML errors by default, including with -f json.
        error_block = re.search(r'(?m)^[ \t]*error:[ \t]*\n((?:[ \t]+[^\n]*(?:\n|$))+)', text)
        if (re.search(r'(?m)^[ \t]*ok:[ \t]*false[ \t]*$', text) and error_block
                and 'code: COMMAND_EXEC' in error_block.group(1)
                and 'Navigation rejected' in error_block.group(1)):
            return True
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            error = data.get('error')
            if error and 'Navigation rejected' in str(error):
                return True
            if data.get('isError') and 'Navigation rejected' in str(data.get('content', '')):
                return True
    return False


def run_case(case, output, round_name):
    if not re.fullmatch(r"[\w-]+", case['id']) or not re.fullmatch(r"[\w-]+", round_name):
        raise ValueError('Invalid case or round identifier')
    folder = Path(output).resolve() / round_name / (case['id'] + '-' + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True)
    started = time.monotonic()
    result = {'case': case['id'], 'platform': case['platform'], 'stage': case['stage'],
              'round': round_name, 'started_at': datetime.now(timezone.utc).isoformat(),
              'task': case['task'], 'assessment': 'unreviewed', 'cache': case.get('cache', 'unknown'),
              'record_schema': 2, 'observation_id': uuid.uuid4().hex,
              'run_root': str(Path(output).resolve()),
              'scenario': case.get('scenario', case['platform']),
              'expected': case.get('expected', []), 'source_urls': case.get('source_urls', [])}
    for key in ('candidate_version', 'artifact_sha256', 'backend', 'attempt', 'recovery_count', 'source_ids', 'route_group', 'analysis_reused',
                'actual_entry', 'execution_id', 'round_mode', 'resume_from', 'recovery_of', 'artifacts'):
        if key in case:
            result[key] = case[key]
    result.update(_failure_metadata(case))
    stdout = stderr = ''
    try:
        if case.get('blocked_reason'):
            result.update(transport='not_attempted', blocked_reason=case['blocked_reason'])
        elif case.get('evidence_file'):
            stdout = Path(case['evidence_file']).read_text()
            result.update(transport='imported', provenance=case.get('provenance', 'Agent observation'))
        else:
            argv = case['argv']
            if not isinstance(argv, list) or not argv or any(not isinstance(x, str) for x in argv):
                raise ValueError('Expected trusted argv array')
            safe_argv(argv)
            env = os.environ.copy()
            if case.get('path_prefix'):
                env['PATH'] = case['path_prefix'] + os.pathsep + env.get('PATH', '')
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, env=env, start_new_session=True)
            try:
                stdout, stderr = proc.communicate(timeout=case.get('timeout', 60))
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                stdout, stderr = proc.communicate()
                result['transport'] = 'timeout'
                stderr += '\nCommand timed out; process group stopped'
            else:
                result.update(transport='returned' if proc.returncode == 0 else 'command_failed', exit_code=proc.returncode)
            result['argv'] = [scrub(x) for x in argv]
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout.decode(errors='replace') if isinstance(error.stdout, bytes) else error.stdout or ''
        stderr = 'Command timed out'
        result['transport'] = 'timeout'
    except FileNotFoundError:
        result['transport'] = 'missing_dependency'
    if _explicit_denial(stdout, stderr):
        result.update(failure_kind='access_denied', failure_stage=case['stage'],
                      failure_scope=case.get('failure_scope', case['platform']))
    elif result.get('transport') in {'timeout', 'missing_dependency'}:
        result.setdefault('failure_kind', result['transport'])
    elif result.get('transport') in {'command_failed', 'not_attempted'}:
        result.setdefault('failure_kind', 'unknown')
    if result.get('failure_kind'):
        result.setdefault('failure_stage', case['stage'])
    result['elapsed_s'] = round(time.monotonic() - started, 3)
    evidence = folder / 'evidence.txt'
    evidence.write_text(scrub(stdout) + '\n\nSTDERR:\n' + scrub(stderr))
    result.update(evidence=str(evidence), evidence_sha256=hashlib.sha256(evidence.read_bytes()).hexdigest())
    write(folder / 'result.json', result)
    return folder / 'result.json'


def review(result_path, assessment):
    path = Path(result_path)
    result = json.loads(path.read_text())
    evidence = Path(result['evidence'])
    if hashlib.sha256(evidence.read_bytes()).hexdigest() != result['evidence_sha256']:
        raise ValueError('Evidence changed')
    verdict = assessment['verdict']
    if verdict not in {'pass', 'partial', 'blocked', 'unsupported', 'empty_valid', 'fail'}:
        raise ValueError('Invalid verdict')
    if not assessment.get('reason'):
        raise ValueError('Assessment needs a reason')
    failure = _failure_metadata(assessment)
    if result.get('failure_kind') == 'access_denied' and failure.get('failure_kind', 'access_denied') != 'access_denied':
        raise ValueError('Explicit denial cannot be reclassified by review')
    if verdict == 'pass':
        if result['transport'] not in {'returned', 'imported'}:
            raise ValueError('Failed command cannot pass')
        if result.get('failure_kind') or failure:
            raise ValueError('Failure observation cannot pass; record a separate recovery attempt')
        quotes = assessment.get('quotes', [])
        if len(set(quotes)) < 2 or any(len(q.strip()) < 8 or q not in evidence.read_text() for q in quotes):
            raise ValueError('Pass needs two actual evidence quotes')
        if result['stage'] == 'analysis' and not all(assessment.get(k) for k in ['conclusion', 'value', 'limitations']):
            raise ValueError('Analysis needs conclusion, value and limitations')
    result.update(failure)
    if verdict in {'fail', 'blocked'}:
        result.setdefault('failure_kind', 'unknown')
        result.setdefault('failure_stage', result['stage'])
    result['assessment'] = verdict
    result['review'] = assessment
    write(path, result)
    return result


def _observation_key(row):
    value = row.get('started_at', '')
    try:
        observed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        timestamp = observed.timestamp()
    except (ValueError, TypeError, AttributeError, OSError):
        timestamp = float('-inf')
    return timestamp, row.get('observation_id', '')


def _entry_key(row):
    return row.get('backend'), row.get('actual_entry', row.get('backend'))


def _latest_attempts(rows):
    latest = {}
    for row in rows:
        key = _entry_key(row)
        def ordering(item):
            # Tied imported timestamps must never choose an old pass by UUID.
            return (_observation_key(item)[0], item.get('assessment') != 'pass',
                    item.get('observation_id', ''))
        if key not in latest or ordering(row) > ordering(latest[key]):
            latest[key] = row
    return list(latest.values())


def _valid_artifacts(row, kinds):
    artifacts = row.get('artifacts', [])
    if not artifacts or any(not _intact(a.get('path'), a.get('sha256')) for a in artifacts):
        return False
    for source in row.get('source_ids', []):
        found = {a.get('kind') for a in artifacts if a.get('source_id') == source}
        if not kinds <= found:
            return False
    return True


def _strict_evidence(row, route, rounds, observed):
    if row.get('record_schema') != 2 or not row.get('observation_id') or not row.get('execution_id') or not row.get('actual_entry'):
        return False
    index = rounds.index(row['round'])
    expected_mode = 'initial' if index == 0 else 'resume' if index == len(rounds) - 1 and len(rounds) > 2 else 'repeat'
    if row.get('round_mode') != expected_mode:
        return False
    if sum(r.get('observation_id') == row['observation_id'] for r in observed) > 1:
        return False  # Relabeling a recorded round is not another observation.
    if expected_mode == 'resume':
        prior = next((r for r in observed if r.get('observation_id') == row.get('resume_from')), None)
        if (not prior or prior.get('round') not in rounds[:index]
                or prior.get('scenario') != row.get('scenario') or prior.get('stage') != row['stage']
                or prior.get('assessment') != 'pass' or not prior.get('execution_id')
                or prior.get('execution_id') == row['execution_id']
                or not row.get('run_root') or prior.get('run_root') != row['run_root']
                or prior.get('cache') not in {'fresh_request', 'fresh_observation'}
                or prior.get('record_schema') != 2
                or set(prior.get('source_ids', [])) != set(row.get('source_ids', []))
                or not _intact(prior.get('evidence'), prior.get('evidence_sha256'))):
            return False
        if row.get('cache') != 'resume_validation':
            return False
    elif row.get('cache') not in {'fresh_request', 'fresh_observation'}:
        return False
    video = route.get('requires_visual', row['platform'] in {'douyin', 'youtube', 'bilibili', 'instagram', 'xiaohongshu'})
    if row['stage'] == 'content':
        kind = 'transcript' if row['platform'] in {'douyin', 'youtube', 'bilibili', 'xiaoyuzhou'} else 'body'
        if not row.get('review', {}).get('content_complete') or not _valid_artifacts(row, {kind}):
            return False
    if row['stage'] == 'analysis':
        kinds = {'report', 'frame'} if video else {'report'}
        if not _valid_artifacts(row, kinds):
            return False
        if video and not row.get('review', {}).get('visual_checked'):
            return False
    return True


def _scope_matches(denied, candidate):
    scope = denied.get('failure_scope', denied['platform'])
    if scope == denied['platform']:
        return True
    targets = candidate.get('source_urls', []) + candidate.get('source_ids', [])
    if not targets or scope in targets:
        return True
    urls = []
    for target in targets:
        try:
            parsed = urlsplit(target)
            if parsed.scheme in {'http', 'https'} and parsed.hostname:
                urls.append(parsed)
        except (ValueError, TypeError):
            continue
    if not urls:
        return True  # Opaque IDs alone cannot prove a denied target is unrelated.
    try:
        if '://' in scope:
            wanted = urlsplit(scope)
            if not wanted.hostname:
                return True
            # A fragment only selects a location within the same requested page.
            def identity(url):
                return urlunsplit((url.scheme.lower(), url.netloc.lower(), url.path or '/', url.query, ''))
            return any(identity(url) == identity(wanted) for url in urls)
        parsed_scope = urlsplit('//' + scope)
        host = (parsed_scope.hostname or '').lower().rstrip('.')
        if not host or '.' not in host or parsed_scope.path or parsed_scope.query or parsed_scope.fragment:
            return True  # Unknown scope is not permission to select a different entry.
        return any((url.hostname or '').lower().rstrip('.') == host or
                   (url.hostname or '').lower().rstrip('.').endswith('.' + host) for url in urls)
    except (ValueError, TypeError):
        return True


def _recovered(candidate, failure, *, explicit=False):
    if (not failure.get('observation_id') or failure['observation_id'] not in candidate.get('recovery_of', [])
            or _observation_key(candidate) <= _observation_key(failure)):
        return False
    if not explicit:
        return True
    recovery = candidate.get('review', {}).get('recovery', {})
    if recovery.get('kind') != 'normal_access_restored' or not recovery.get('reason'):
        return False
    try:
        evidence = Path(candidate['evidence']).read_text()
    except OSError:
        return False
    quotes = recovery.get('quotes', [])
    return len(set(quotes)) >= 2 and all(len(q.strip()) >= 8 and q in evidence for q in quotes)


def route_assessments(rows, planned):
    """Certify current attempts; legacy observations never gain fresh evidence."""
    routes = {}
    strict = planned.get('acceptance_schema', 1) >= 2
    for route in planned.get('planned_routes', []):
        platform = route['platform']
        observed = [r for r in rows if r['platform'] == platform]
        current = [r for r in observed if not planned.get('candidate_version') or
                   r.get('candidate_version') == planned['candidate_version']]
        rounds = route.get('required_rounds', ['r1', 'r2', 'r3'])
        tasks = route.get('required_scenarios', [platform])
        checks = []
        fallback_used = False
        denied = [r for r in observed if r.get('failure_kind') == 'access_denied']
        for task in tasks:
            for round_name in rounds:
                selected = {}
                missing = []
                effective = {}
                for stage in ('discovery', 'content', 'analysis'):
                    primary = route.get('formal_backends', {}).get(stage, [])
                    backup = route.get('fallback_backends', {}).get(stage, [])
                    stage_rows = [r for r in current if r.get('scenario') == task
                                  and r['round'] == round_name and r['stage'] == stage
                                  and r.get('backend') in primary + backup]
                    latest = _latest_attempts(stage_rows)
                    effective[stage] = [r.get('observation_id', r.get('case')) for r in latest]
                    valid = []
                    for row in latest:
                        if (row['assessment'] != 'pass' or row.get('failure_kind')
                                or row.get('cache') not in ('fresh_request', 'fresh_observation', 'resume_validation')
                                or not row.get('source_ids')
                                or not _intact(row.get('evidence'), row.get('evidence_sha256'))):
                            continue
                        if strict and not _strict_evidence(row, route, rounds, current):
                            continue
                        if any(_scope_matches(d, row) and not _recovered(row, d, explicit=True) for d in denied):
                            continue
                        failures = [r for r in stage_rows if r['assessment'] in {'fail', 'blocked'}]
                        if strict and any(_entry_key(f) == _entry_key(row) and not _recovered(row, f) for f in failures):
                            continue
                        if strict and any(_entry_key(f) != _entry_key(row)
                                          and f.get('failure_kind', 'unknown') == 'unknown'
                                          and _observation_key(f) < _observation_key(row) for f in failures):
                            continue
                        if row.get('backend') in backup:
                            # A fallback may recover a known ordinary failure, not an unknown rejection.
                            primary_failures = [f for f in failures if f.get('backend') in primary]
                            if any(f.get('failure_kind', 'unknown') == 'unknown' for f in primary_failures):
                                continue
                            if strict and any(not _recovered(row, f) for f in primary_failures):
                                continue
                        valid.append(row)
                    if valid:
                        selected[stage] = max(valid, key=lambda r: (r.get('backend') in primary, _observation_key(r)))
                    else:
                        missing.append(stage)
                if not missing:
                    discovered = set(selected['discovery']['source_ids'])
                    content = set(selected['content']['source_ids'])
                    analyzed = set(selected['analysis']['source_ids'])
                    if not content <= discovered or analyzed != content:
                        missing.append('source_identity_or_analysis_coverage')
                    else:
                        fallback_used |= any(row['backend'] not in route.get('formal_backends', {}).get(stage, [])
                                             for stage, row in selected.items())
                checks.append({'scenario': task, 'round': round_name, 'missing': missing,
                               'effective_attempts': effective,
                               'selected_attempts': {stage: row.get('observation_id', row.get('case')) for stage, row in selected.items()}})
        complete = bool(checks) and all(not c['missing'] for c in checks)
        if complete:
            verdict = 'fallback_pass' if fallback_used else 'pass'
        elif not current:
            verdict = 'not_tested'
        elif any(r['assessment'] == 'pass' for r in current):
            verdict = 'partial'
        elif any(r['assessment'] == 'blocked' or r.get('failure_kind') == 'access_denied' for r in current):
            verdict = 'blocked'
        elif any(r['assessment'] == 'fail' for r in current):
            verdict = 'fail'
        else:
            verdict = 'partial'
        routes[platform] = {'group': route.get('route_group', 'unspecified'),
                            'verdict': verdict, 'checks': checks,
                            'observations': len(observed), 'acceptance_schema': 2 if strict else 1,
                            'primary_failures': sum(r['assessment'] in ('fail', 'blocked') and
                                                    r.get('backend') in (route.get('formal_backends', {}).get(r['stage'], [])
                                                        if r['stage'] != 'connection' else
                                                        [backend for backends in route.get('formal_backends', {}).values() for backend in backends])
                                                    for r in observed),
                            'failures': [{key: r.get(key) for key in ('observation_id', 'case', 'round', 'stage', 'backend', 'actual_entry', 'failure_kind', 'failure_stage', 'failure_scope', 'recovery_of')}
                                         for r in observed if r['assessment'] in ('fail', 'blocked') or r.get('failure_kind')],
                            'empty_results': sum(r['assessment'] == 'empty_valid' for r in observed),
                            'replays': sum(r.get('cache') == 'cached_replay' for r in observed)}
    groups = {}
    for route in routes.values():
        group = groups.setdefault(route['group'], {'total': 0, 'pass': 0, 'fallback_pass': 0,
                                                   'partial': 0, 'blocked': 0, 'fail': 0, 'not_tested': 0})
        group['total'] += 1
        group[route['verdict']] += 1
    return {'routes': routes, 'groups': groups}


def summary(output):
    rows = [json.loads(p.read_text()) for p in Path(output).glob('*/*/result.json')]
    platforms: dict[str, Any] = {}
    for row in rows:
        group = platforms.setdefault(row['platform'], {'observations': 0, 'passed': 0, 'unreviewed': 0, 'cases': {}})
        group['observations'] += 1
        group['passed'] += row['assessment'] == 'pass'
        group['unreviewed'] += row['assessment'] == 'unreviewed'
        case = group['cases'].setdefault(row['case'], {'stage': row['stage'], 'rounds': {}, 'elapsed_s': []})
        case['rounds'].setdefault(row['round'], []).append({
            'assessment': row['assessment'], 'transport': row['transport'],
            'cache': row['cache'], 'evidence': row['evidence'],
            **{key: row.get(key) for key in ('observation_id', 'actual_entry', 'failure_kind', 'failure_stage', 'failure_scope', 'recovery_of')}})
        case['elapsed_s'].append(row['elapsed_s'])
    coverage = {}
    manifest = Path(output) / 'run.json'
    planned = {}
    if manifest.exists():
        planned = json.loads(manifest.read_text())
        for route in planned.get('planned_routes', []):
            platform = route['platform']
            observed = [r for r in rows if r['platform'] == platform]
            # Coverage is not certification. Even many passing observations may
            # omit a required task, use a different backend, or replay old data.
            coverage[platform] = {
                'recorded': bool(observed),
                'rounds_recorded': sorted({r['round'] for r in observed}),
                'unreviewed': sum(r['assessment'] == 'unreviewed' for r in observed),
                'versions': sorted({r.get('candidate_version', 'unknown') for r in observed}),
                'backends': sorted({r.get('backend', 'unknown') for r in observed}),
                'cached_replays': sum(r.get('cache') == 'cached_replay' for r in observed),
            }
    return {'platforms': platforms, 'planned_coverage': coverage,
            'task_acceptance': route_assessments(rows, planned),
            'note': 'Manual stage verdicts are not end-to-end certification. A fresh request may still hit an upstream cache. Repeated rounds in one session do not establish long-term reliability.'}


def run_cases(cases, output, round_name):
    """Only explicit denials stop dependent access; initialization is entry-local."""
    existing = [json.loads(p.read_text()) for p in Path(output).glob('*/*/result.json')]
    denied = [r for r in existing if r.get('failure_kind') == 'access_denied' and not any(
        candidate.get('platform') == r['platform'] and candidate.get('assessment') == 'pass'
        and _scope_matches(r, candidate) and _intact(candidate.get('evidence'), candidate.get('evidence_sha256'))
        and _recovered(candidate, r, explicit=True) for candidate in existing)]
    for original in cases:
        case = dict(original)
        failure = next((r for r in denied if r['platform'] == case['platform'] and _scope_matches(r, case)), None)
        if failure and not case.get('evidence_file'):
            case.update(blocked_reason='Earlier explicit access denial; dependent requests stopped',
                        failure_kind='access_denied', failure_stage=case['stage'],
                        failure_scope=failure.get('failure_scope', case['platform']))
        path = run_case(case, output, round_name)
        result = json.loads(path.read_text())
        if result.get('failure_kind') == 'access_denied':
            denied.append(result)
        yield path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    p = sub.add_parser('run')
    p.add_argument('cases', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--round', required=True)
    p = sub.add_parser('review')
    p.add_argument('result', type=Path)
    p.add_argument('assessment', type=Path)
    p = sub.add_parser('summary')
    p.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'run':
        for path in run_cases(json.loads(args.cases.read_text()), args.output, args.round):
            print(json.dumps({'result': str(path)}, ensure_ascii=False), flush=True)
    elif args.action == 'review':
        print(json.dumps(review(args.result, json.loads(args.assessment.read_text())), ensure_ascii=False))
    else:
        print(json.dumps(summary(args.output), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
