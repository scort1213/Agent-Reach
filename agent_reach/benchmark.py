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


def run_case(case, output, round_name):
    if not re.fullmatch(r"[\w-]+", case['id']) or not re.fullmatch(r"[\w-]+", round_name):
        raise ValueError('Invalid case or round identifier')
    folder = Path(output).resolve() / round_name / (case['id'] + '-' + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True)
    started = time.monotonic()
    result = {'case': case['id'], 'platform': case['platform'], 'stage': case['stage'],
              'round': round_name, 'started_at': datetime.now(timezone.utc).isoformat(),
              'task': case['task'], 'assessment': 'unreviewed', 'cache': case.get('cache', 'unknown'),
              'scenario': case.get('scenario', case['platform']),
              'expected': case.get('expected', []), 'source_urls': case.get('source_urls', [])}
    for key in ('candidate_version', 'artifact_sha256', 'backend', 'attempt', 'recovery_count', 'source_ids', 'route_group', 'analysis_reused'):
        if key in case:
            result[key] = case[key]
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
    if verdict == 'pass':
        quotes = assessment.get('quotes', [])
        if len(set(quotes)) < 2 or any(len(q.strip()) < 8 or q not in evidence.read_text() for q in quotes):
            raise ValueError('Pass needs two actual evidence quotes')
        if result['stage'] == 'analysis' and not all(assessment.get(k) for k in ['conclusion', 'value', 'limitations']):
            raise ValueError('Analysis needs conclusion, value and limitations')
        if result['transport'] not in {'returned', 'imported'}:
            raise ValueError('Failed command cannot pass')
    result['assessment'] = verdict
    result['review'] = assessment
    write(path, result)
    return result


def route_assessments(rows, planned):
    """Certify declared task stages, never platform probe counts or replay alone."""
    routes = {}
    for route in planned.get('planned_routes', []):
        platform = route['platform']
        observed = [r for r in rows if r['platform'] == platform]
        rounds = route.get('required_rounds', ['r1', 'r2', 'r3'])
        tasks = route.get('required_scenarios', [platform])
        checks = []
        fallback_used = False
        for task in tasks:
            for round_name in rounds:
                selected = {}
                missing = []
                for stage in ('discovery', 'content', 'analysis'):
                    primary = route.get('formal_backends', {}).get(stage, [])
                    backup = route.get('fallback_backends', {}).get(stage, [])
                    candidates = [r for r in observed
                                  if r.get('scenario') == task and r['round'] == round_name
                                  and r['stage'] == stage and r['assessment'] == 'pass'
                                  and r.get('cache') in ('fresh_request', 'fresh_observation', 'resume_validation')
                                  and r.get('backend') in primary + backup
                                  and r.get('source_ids')
                                  and (not planned.get('candidate_version') or
                                       r.get('candidate_version') == planned['candidate_version'])]
                    valid = []
                    for row in candidates:
                        evidence = Path(row['evidence'])
                        if evidence.is_file() and hashlib.sha256(evidence.read_bytes()).hexdigest() == row['evidence_sha256']:
                            valid.append(row)
                    if valid:
                        selected[stage] = max(valid, key=lambda r: (r.get('backend') in primary, r.get('started_at', '')))
                    else:
                        missing.append(stage)
                if not missing:
                    discovered = set(selected['discovery']['source_ids'])
                    content = set(selected['content']['source_ids'])
                    analyzed = set(selected['analysis']['source_ids'])
                    if not content <= discovered or analyzed != content:
                        missing.append('source_identity_or_analysis_coverage')
                    else:
                        fallback_used |= any(row['backend'] not in route['formal_backends'].get(stage, [])
                                             for stage, row in selected.items())
                checks.append({'scenario': task, 'round': round_name, 'missing': missing})
        complete = bool(checks) and all(not c['missing'] for c in checks)
        if complete:
            verdict = 'fallback_pass' if fallback_used else 'pass'
        elif not observed:
            verdict = 'not_tested'
        elif any(r['assessment'] == 'pass' for r in observed):
            verdict = 'partial'
        elif any(r['assessment'] == 'blocked' for r in observed):
            verdict = 'blocked'
        elif any(r['assessment'] == 'fail' for r in observed):
            verdict = 'fail'
        else:
            verdict = 'partial'
        routes[platform] = {'group': route.get('route_group', 'unspecified'),
                            'verdict': verdict, 'checks': checks,
                            'observations': len(observed),
                            'primary_failures': sum(r['assessment'] in ('fail', 'blocked') for r in observed),
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
            'cache': row['cache'], 'evidence': row['evidence']})
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
    """Stop the same platform after an explicit tool navigation denial."""
    blocked: dict[str, str] = {}
    for original in cases:
        case = dict(original)
        if case['platform'] in blocked:
            case['blocked_reason'] = blocked[case['platform']]
        path = run_case(case, output, round_name)
        result = json.loads(path.read_text())
        if 'Navigation rejected' in Path(result['evidence']).read_text():
            blocked[case['platform']] = 'Earlier tool navigation denial; dependent requests stopped'
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
