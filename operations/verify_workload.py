"""Verify retained hosted-workload consistency offline using only the stdlib.

This checks published records and exact local Git source bytes. It makes no HTTP,
Redis or model request and does not attest that the reported observations occurred.
It cannot recover omitted response bodies or establish deployment cleanup, real
provider quality, production traffic, uptime or an SLA.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / 'operations/results/hosted-staging-verified-config-2026-10-25.json'
SOURCE_FILES = ('app.py', 'staging.py', 'engine.py', 'grounding.py', 'observability.py', 'prepare.py',
                'data/corpus.json', 'model_manifest.json', 'requirements.txt', 'vercel.json')
EXPECTED = {'queries': 16, 'answered': 5, 'refused': 1, 'errors': 10,
            'status_2xx': 6, 'status_4xx': 7, 'status_5xx': 3,
            'provider_responses': 8, 'usage_complete': 7, 'usage_missing': 1,
            'prompt_tokens': 700, 'output_tokens': 140, 'feedback_issued': 5,
            'feedback_total': 0, 'latency_le_300000_ms': 16}
BOUNDS = {'query_requests': 16, 'auxiliary_gets': 6, 'max_concurrency': 8, 'daily_admissions': 9,
          'paid_provider_requests': 0, 'response_bytes': 131072, 'socket_timeout_seconds': 125}
LATENCY_BOUNDS = (100, 500, 1000, 3000, 10000, 30000, 60000, 300000)
FEEDBACK = {'feedback_helpful', 'feedback_unhelpful', 'feedback_total'} | {
    'feedback_category_' + category for category in ('clear', 'incomplete', 'incorrect', 'citation_issue', 'other')}
FIELDS = set(EXPECTED) | FEEDBACK | {'duration_ms_total'} | {f'latency_le_{bound}_ms' for bound in LATENCY_BOUNDS}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def integer(value, label, minimum=0, maximum=None):
    require(type(value) is int and value >= minimum and (maximum is None or value <= maximum), 'Invalid integer: ' + label)
    return value


def finite(value, label, maximum=None):
    require(type(value) in (int, float) and math.isfinite(value) and value >= 0
            and (maximum is None or value <= maximum), 'Invalid finite nonnegative timing: ' + label)
    return value


def timestamp(value):
    require(isinstance(value, str) and 'T' in value, 'Invalid ISO UTC timestamp')
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError('Invalid ISO UTC timestamp') from None
    require(parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0), 'Timestamp must identify UTC')
    return parsed


def digest(content):
    return hashlib.sha256(content).hexdigest()


@lru_cache(maxsize=128)
def git_bytes(repo, source, path):
    """Read exact committed bytes, independent of working-tree line endings."""
    require(re.fullmatch(r'[a-f0-9]{40}', source) is not None, 'Invalid committed source SHA')
    require(path in {'web/' + name for name in SOURCE_FILES} |
            {'operations/hosted_workload.py', 'evaluation/requirements.lock.txt'}, 'Unexpected Git source path')
    try:
        return subprocess.run(['git', '-C', str(repo), 'show', source + ':' + path],
                              capture_output=True, check=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        raise ValueError('Committed workload source is unavailable in local Git history') from None


def literal_assignment(content, name):
    for node in ast.parse(content).body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError('Frozen probe protocol assignment is missing: ' + name)


def _validate_report(report, repo=ROOT):
    require(report.get('passed') is True and not report.get('failure'), 'Report is not a complete successful workload')
    require(report['schema_version'] == 1, 'Unsupported workload report schema')
    require(report['bounds'] == BOUNDS and all(type(value) is int for value in report['bounds'].values()), 'Workload bounds mismatch')
    started, completed = timestamp(report['started_at_utc']), timestamp(report['completed_at_utc'])
    require(started <= completed, 'Workload timestamps reversed')
    source, run = report['source_sha'], report['run_id']
    require(re.fullmatch(r'[a-f0-9]{40}', source) is not None and re.fullmatch(r'[a-f0-9]{12,32}', run) is not None, 'Invalid workload identity')
    require(re.fullmatch(r'[a-z0-9-]+-[a-z0-9]{8,12}-[a-z0-9-]+\.vercel\.app', report['target_host']) is not None, 'Target is not an immutable preview hostname')
    probe = git_bytes(repo, source, 'operations/hosted_workload.py')
    require(digest(probe) == report['probe_sha256'], 'Probe source hash mismatch')
    require(literal_assignment(probe, 'EXPECTED') == EXPECTED and literal_assignment(probe, 'SOURCE_FILES') == SOURCE_FILES, 'Frozen probe protocol mismatch')
    source_bytes = {name: git_bytes(repo, source, 'web/' + name) for name in SOURCE_FILES}
    require(report['source_sha256'] == {name: digest(content) for name, content in source_bytes.items()}, 'Committed runtime source hash mismatch')
    provenance = report['provenance']
    manifest = json.loads(source_bytes['model_manifest.json'])
    expected_provenance = {'schema_version': 1, 'environment': 'preview', 'run_id': run, 'source_sha': source,
        'source_sha256': report['source_sha256'],
        'embedding_artifacts_sha256': {name: info['sha256'] for name, info in manifest['files'].items()},
        'provider_mode': 'deterministic-injected-no-network', 'provider_requests': 0, 'model': 'deterministic-fixture-v1',
        'rate_limit_prefix': f'docwise:staging:{run}:admission', 'operations_prefix': f'docwise:staging:{run}:operations',
        'limits': {'global_per_minute': 20, 'client_per_minute': 3, 'daily_admissions': 9},
        'fixture_usage': {'prompt_tokens': 100, 'completion_tokens': 20},
        'fixture_rates_usd_per_million': {'prompt': '1', 'output': '2'}, 'deployment_host': report['target_host']}
    require(set(provenance) == set(expected_provenance) | {'deployment_id', 'runtime_versions'}, 'Provenance field inventory mismatch')
    for key, expected in expected_provenance.items():
        require(provenance[key] == expected, 'Provenance mismatch: ' + key)
    require(type(provenance['provider_requests']) is int and provenance['provider_requests'] == 0, 'Real provider work cannot be workload evidence')
    require(re.fullmatch(r'dpl_[A-Za-z0-9]{8,80}', provenance['deployment_id']) is not None, 'Invalid deployment identity')
    versions = provenance['runtime_versions']
    packages = {'fastapi', 'numpy', 'onnxruntime', 'tokenizers', 'rank-bm25'}
    require(set(versions) == packages | {'python'}, 'Runtime package inventory mismatch')
    require(re.fullmatch(r'3\.12\.\d+', versions['python']) is not None, 'Unexpected Python runtime version')
    lock = git_bytes(repo, source, 'evaluation/requirements.lock.txt').decode('utf-8')
    locked = dict(re.findall(r'^([A-Za-z0-9_-]+)==([^\r\n]+)$', lock, flags=re.MULTILINE))
    require(all(versions[name] == locked[name] for name in packages), 'Reported runtime differs from frozen evaluation packages')

    allowed_labels = {f"[Source: {chunk['metadata']['doc_title']}, Section: {chunk['metadata']['section_title']}]"
                      for chunk in json.loads(source_bytes['data/corpus.json'])}
    rows = report['requests']
    require(len(rows) == 16 and [row['sequence'] for row in rows] == list(range(1, 17))
            and all(type(row['sequence']) is int for row in rows), 'Query sequence must contain exactly 16 unique ordered requests')

    def check_meta(row):
        begin, end = timestamp(row['started_at_utc']), timestamp(row['completed_at_utc'])
        require(started <= begin <= end <= completed, 'Request timestamp outside workload interval')
        finite(row['elapsed_ms'], 'elapsed_ms')
        require(not row.get('error'), 'Request transport or JSON error is retained')
        require(re.fullmatch(r'[a-f0-9]{64}', row['response_sha256']) is not None, 'Invalid response hash')
        integer(row['http_status'], 'http_status', 100, 599)
        return begin, end

    intervals = [check_meta(row) for row in rows]
    fixed = [('guards_and_client_limit', 'valid', 200), ('guards_and_client_limit', 'invalid_citation', 502),
             ('guards_and_client_limit', 'provider_error', 503), ('guards_and_client_limit', 'valid', 429),
             ('usage_and_refusal', 'missing_usage', 200), ('usage_and_refusal', 'truncated', 502),
             ('usage_and_refusal', 'refusal', 200)]
    require([(row['phase'], row['scenario'], row['http_status']) for row in rows[:7]] == fixed, 'Guard and usage phase protocol mismatch')
    concurrent = rows[7:15]
    require(all(row['phase'] == 'concurrent_admission' and row['scenario'] == 'slow' for row in concurrent)
            and Counter(row['http_status'] for row in concurrent) == {200: 3, 429: 5}, 'Concurrent admission protocol mismatch')
    require((rows[-1]['phase'], rows[-1]['scenario'], rows[-1]['http_status']) == ('daily_budget', 'valid', 429), 'Daily budget phase mismatch')
    statuses = Counter(row['http_status'] for row in rows)
    require(statuses == {200: 6, 429: 7, 502: 2, 503: 1}, 'HTTP status denominator mismatch')
    for row in rows:
        if row['http_status'] == 429:
            integer(row.get('retry_after_seconds'), 'retry_after_seconds', 1, 86400)
        if row['http_status'] == 200:
            require(row['result_valid'] is True and type(row['retrieval_source_count']) is int
                    and row['retrieval_source_count'] == 5, 'Invalid retained answer or retrieval count')
            labels = row['retrieved_labels']
            require(isinstance(labels, list) and len(labels) == len(set(labels)) == 5
                    and all(label in allowed_labels for label in labels), 'Retrieved labels are not five committed corpus labels')
            finite(row['handler_elapsed_seconds'], 'handler_elapsed_seconds', 300)
            integer(row['client_ttl_ms'], 'client_ttl_ms', 1, 60000)
    require(rows[-1]['retry_after_seconds'] > 60, 'Daily denial lacks a daily-window Retry-After')
    active = peak = 0
    for _, change in sorted([(begin, 1) for begin, _ in intervals[7:15]] + [(end, -1) for _, end in intervals[7:15]]):
        active += change
        peak = max(peak, active)
    require(peak == report['bounds']['max_concurrency'] == 8, 'Retained client concurrency does not reach the declared eight requests')
    waits = report['waits']
    require(len(waits) == 3, 'Expected exactly three bounded Redis-window waits')
    for wait, group, next_index in zip(waits, ([rows[3]], rows[4:7], concurrent), (4, 7, 15)):
        durations = [row['client_ttl_ms'] / 1000 if type(row.get('client_ttl_ms')) is int
                     else row['retry_after_seconds'] for row in group
                     if type(row.get('client_ttl_ms')) is int or type(row.get('retry_after_seconds')) is int
                     and 1 <= row['retry_after_seconds'] <= 60]
        require(durations and wait['reason'] == 'real_redis_admission_window', 'Missing bounded Redis-window evidence')
        require(finite(wait['seconds'], 'wait_seconds', 60.25) == round(max(durations) + .25, 3), 'Redis wait differs from observed TTL plus guard')
        end = max(timestamp(row['completed_at_utc']) for row in group)
        require((intervals[next_index][0] - end).total_seconds() >= wait['seconds'] - .01, 'Next phase starts before retained Redis wait expires')
    aux = report['auxiliary_requests']
    require([row['endpoint'] for row in aux] == ['/api/staging/provenance', '/api/health',
            '/api/operations?days=2', '/api/operations?days=2'] and len(aux) <= BOUNDS['auxiliary_gets'], 'Auxiliary endpoint inventory mismatch')
    for row in aux:
        check_meta(row)
        require(row['http_status'] == 200, 'Auxiliary endpoint was unsuccessful')

    accounting = report['accounting']
    cohorts = accounting['cohort_deltas']
    require(set(cohorts) == {'visitor', 'synthetic'}, 'Cohort inventory mismatch')
    for scope, values in cohorts.items():
        require(set(values) == FIELDS, 'Aggregate counter field inventory mismatch')
        for field, value in values.items():
            integer(value, scope + '.' + field)
    require(not any(cohorts['visitor'].values()), 'Visitor changes cannot be claimed as synthetic evidence')
    synthetic = cohorts['synthetic']
    for key, value in EXPECTED.items():
        require(synthetic[key] == value, 'Synthetic aggregate mismatch: ' + key)
    require(not any(synthetic[field] for field in FEEDBACK), 'Workload contains no actual visitor feedback')
    cumulative = [synthetic[f'latency_le_{bound}_ms'] for bound in LATENCY_BOUNDS]
    require(cumulative == sorted(cumulative) and all(value <= 16 for value in cumulative), 'Invalid cumulative latency buckets')
    require(sum(row.get('handler_elapsed_seconds', 0) for row in rows)*1000 <= synthetic['duration_ms_total'] <= 16*300000,
            'Aggregate handler duration outside retained successful timings and cap')
    require(accounting['rates_are_fixture_arithmetic_only'] is True, 'Fixture cost must be identified as arithmetic only')
    require(type(accounting['actual_paid_provider_requests']) is int and accounting['actual_paid_provider_requests'] == 0,
            'Actual paid provider requests must remain zero')
    try:
        cost = accounting['fixture_estimate_usd']
        require(set(cost) == {'visitor', 'synthetic'} and Decimal(cost['visitor']) == 0
                and Decimal(cost['synthetic']) == (Decimal(700) + Decimal(140)*2)/Decimal(1000000)
                and Decimal(accounting['actual_paid_provider_spend_usd']) == 0, 'Fixture cost arithmetic or actual paid spend mismatch')
    except (InvalidOperation, TypeError):
        raise ValueError('Invalid decimal cost evidence') from None
    return {'verified': True, 'query_requests_verified': len(rows), 'auxiliary_requests_verified': len(aux),
            'http_status_counts': {str(code): count for code, count in sorted(statuses.items())},
            'source_sha': source, 'waits_verified': 3, 'observed_client_concurrency': peak,
            'fixture_estimate_usd': '0.00098', 'actual_paid_provider_requests': 0,
            'independent_attestation': False,
            'limitations': 'Offline consistency and exact local Git source binding only; no attestation of HTTP observations, deployment cleanup, provider quality, visitor outcomes or production uptime.'}


def validate_report(report, repo=ROOT):
    try:
        return _validate_report(report, repo)
    except (KeyError, TypeError, IndexError):
        raise ValueError('Malformed workload evidence schema') from None


def verify_report(path=REPORT):
    def reject_constant(_):
        raise ValueError('Non-finite JSON number')
    report = json.loads(path.read_text(encoding='utf-8-sig'), parse_constant=reject_constant)
    return validate_report(report)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, default=REPORT)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(verify_report(args.report), sort_keys=True, allow_nan=False))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as error:
        print('Workload evidence verification failed: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
