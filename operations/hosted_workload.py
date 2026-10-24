"""Opt-in 16-query Vercel preview diagnostic with injected provider and zero model calls."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from operations.collect import safe_snapshot

SOURCE_FILES = ('app.py', 'staging.py', 'engine.py', 'grounding.py', 'observability.py', 'prepare.py',
                'data/corpus.json', 'model_manifest.json', 'requirements.txt', 'vercel.json')
REFUSAL = 'I could not find an answer to this question in the provided documentation.'
EXPECTED = {'queries': 16, 'answered': 5, 'refused': 1, 'errors': 10,
            'status_2xx': 6, 'status_4xx': 7, 'status_5xx': 3,
            'provider_responses': 8, 'usage_complete': 7, 'usage_missing': 1,
            'prompt_tokens': 700, 'output_tokens': 140, 'feedback_issued': 5,
            'feedback_total': 0, 'latency_le_300000_ms': 16}


class ProbeFailure(Exception):
    """Fixed safe diagnostic failure; never include headers or response text."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def http(base, path, headers, payload=None):
    started, stamp = time.perf_counter(), utc_now()
    request = Request(base+path, data=None if payload is None else json.dumps(payload).encode(),
                      headers=dict(headers, **{'Accept': 'application/json', 'Content-Type': 'application/json'}))
    meta = {'started_at_utc': stamp, 'http_status': None}
    body = None
    try:
        try:
            response = build_opener(NoRedirect).open(request, timeout=125)
        except HTTPError as error:
            response = error
        with response:
            meta['http_status'] = response.status
            retry = response.headers.get('Retry-After', '')
            if re.fullmatch(r'\d{1,5}', retry) and 1 <= int(retry) <= 86400:
                meta['retry_after_seconds'] = int(retry)
            raw = response.read(131073)
            if len(raw) > 131072:
                meta['error'] = 'response_too_large'
            else:
                meta['response_sha256'] = hashlib.sha256(raw).hexdigest()
                body = json.loads(raw)
    except Exception:
        meta['error'] = 'request_or_json_failed'
    meta.update(elapsed_ms=round((time.perf_counter()-started)*1000, 3), completed_at_utc=utc_now())
    return meta, body


def validate_provenance(body, source, run, hashes, artifacts, host=None):
    expected = {'schema_version': 1, 'environment': 'preview', 'run_id': run, 'source_sha': source,
                'source_sha256': hashes, 'embedding_artifacts_sha256': artifacts,
                'provider_mode': 'deterministic-injected-no-network', 'provider_requests': 0,
                'model': 'deterministic-fixture-v1',
                'rate_limit_prefix': f'docwise:staging:{run}:admission',
                'operations_prefix': f'docwise:staging:{run}:operations',
                'limits': {'global_per_minute': 20, 'client_per_minute': 3, 'daily_admissions': 9},
                'fixture_usage': {'prompt_tokens': 100, 'completion_tokens': 20},
                'fixture_rates_usd_per_million': {'prompt': '1', 'output': '2'}}
    if not isinstance(body, dict) or any(body.get(key) != value for key, value in expected.items()):
        raise ProbeFailure('provenance_mismatch')
    if host is not None:
        versions = body.get('runtime_versions')
        if (body.get('deployment_host') != host
                or not re.fullmatch(r'dpl_[A-Za-z0-9]{8,80}', str(body.get('deployment_id', '')))
                or not isinstance(versions, dict)
                or set(versions) != {'python', 'fastapi', 'numpy', 'onnxruntime', 'tokenizers', 'rank-bm25'}
                or any(not isinstance(value, str) or not re.fullmatch(r'[0-9][A-Za-z0-9.+_-]{0,63}', value) for value in versions.values())):
            raise ProbeFailure('deployment_identity_or_runtime_mismatch')
        expected.update(deployment_host=host, deployment_id=body['deployment_id'], runtime_versions=versions)
    return expected


class Runner:
    def __init__(self, base, controls, report, persist, request=http, sleeper=time.sleep):
        self.base, self.report, self.persist = base, report, persist
        self.request, self.sleeper = request, sleeper
        self.headers = {'x-staging-token': controls.get('STAGING_WORKLOAD_TOKEN', ''),
                        'x-ops-token': controls.get('OPS_READ_TOKEN', '')}
        if controls.get('VERCEL_AUTOMATION_BYPASS_SECRET'):
            self.headers['x-vercel-protection-bypass'] = controls['VERCEL_AUTOMATION_BYPASS_SECRET']
        self.lock = threading.Lock()
        self.sent = 0

    def read(self, path):
        if len(self.report['auxiliary_requests']) >= 6:
            raise ProbeFailure('auxiliary_request_bound')
        meta, body = self.request(self.base, path, self.headers)
        self.report['auxiliary_requests'].append(dict(meta, endpoint=path))
        self.persist()
        if meta.get('http_status') != 200 or meta.get('error'):
            raise ProbeFailure('auxiliary_endpoint_failed')
        return body

    def query(self, scenario, phase):
        with self.lock:
            if self.sent >= 16:
                raise ProbeFailure('query_request_bound')
            self.sent += 1
            sequence = self.sent
        meta, body = self.request(self.base, '/api/query', dict(self.headers, **{'x-staging-scenario': scenario}),
                                  {'question': 'What is FastMCP and how are tools defined?'})
        row = dict(meta, sequence=sequence, phase=phase, scenario=scenario)
        if isinstance(body, dict) and meta.get('http_status') == 200:
            elapsed = body.get('elapsed_seconds')
            row['handler_elapsed_seconds'] = elapsed if type(elapsed) in (int, float) and math.isfinite(elapsed) and 0 <= elapsed <= 300 else None
            ttl = (body.get('staging_admission') or {}).get('client_ttl_ms')
            if type(ttl) is int and 1 <= ttl <= 60000:
                row['client_ttl_ms'] = ttl
            sources, citations = body.get('sources'), body.get('citations')
            sources_valid = isinstance(sources, list) and len(sources) == 5 and all(isinstance(item, dict) and isinstance(item.get('document'), str) and isinstance(item.get('section'), str) and item.get('rank') == index for index, item in enumerate(sources, 1))
            allowed = {f"[Source: {item['document']}, Section: {item['section']}]" for item in sources} if sources_valid else set()
            row['retrieval_source_count'] = len(sources) if sources_valid else None
            row['retrieved_labels'] = sorted(allowed)
            row['result_valid'] = (body.get('model') == 'deterministic-fixture-v1' and sources_valid
                                   and row['handler_elapsed_seconds'] is not None and 'client_ttl_ms' in row
                                   and isinstance(citations, list) and all(isinstance(label, str) and label in allowed for label in citations)
                                   and ((scenario == 'refusal' and body.get('answer') == REFUSAL and body.get('refused') is True and not citations)
                                        or (scenario != 'refusal' and body.get('refused') is False and bool(citations))))
        with self.lock:
            self.report['requests'].append(row)
            self.report['requests'].sort(key=lambda item: item['sequence'])
            self.persist()
        return row

    def expect(self, row, status):
        if row.get('http_status') != status or row.get('error') or (status == 200 and not row.get('result_valid')):
            raise ProbeFailure('query_result_mismatch')
        if status == 429 and 'retry_after_seconds' not in row:
            raise ProbeFailure('missing_admission_ttl')

    def wait_window(self, rows):
        durations = []
        for row in rows:
            if type(row.get('client_ttl_ms')) is int and 1 <= row['client_ttl_ms'] <= 60000:
                durations.append(row['client_ttl_ms']/1000)
            elif type(row.get('retry_after_seconds')) is int and 1 <= row['retry_after_seconds'] <= 60:
                durations.append(row['retry_after_seconds'])
        if not durations:
            raise ProbeFailure('unavailable_or_unbounded_window_ttl')
        remaining = max(durations)+.25
        self.report['waits'].append({'reason': 'real_redis_admission_window', 'seconds': round(remaining, 3)})
        self.persist()
        print(f'Waiting {remaining:.3f}s for the observed isolated client window.', flush=True)
        while remaining > 0:
            part = min(30, remaining)
            self.sleeper(part)
            remaining -= part


def run_phases(runner):
    first = []
    for scenario, status in [('valid', 200), ('invalid_citation', 502), ('provider_error', 503), ('valid', 429)]:
        row = runner.query(scenario, 'guards_and_client_limit')
        runner.expect(row, status)
        first.append(row)
    runner.wait_window([first[-1]])
    second = []
    for scenario, status in [('missing_usage', 200), ('truncated', 502), ('refusal', 200)]:
        row = runner.query(scenario, 'usage_and_refusal')
        runner.expect(row, status)
        second.append(row)
    runner.wait_window(second)
    with ThreadPoolExecutor(max_workers=8) as pool:
        third = list(pool.map(lambda _: runner.query('slow', 'concurrent_admission'), range(8)))
    if sorted(row.get('http_status') or 0 for row in third) != [200]*3+[429]*5:
        raise ProbeFailure('concurrent_admission_mismatch')
    for row in third:
        runner.expect(row, row['http_status'])
    runner.wait_window(third)
    final = runner.query('valid', 'daily_budget')
    runner.expect(final, 429)
    if final['retry_after_seconds'] <= 60:
        raise ProbeFailure('daily_budget_not_observed')


def verify_aggregates(before, after):
    clean_before, clean_after = safe_snapshot(before), safe_snapshot(after)
    if any(value for scope in clean_before['totals'].values() for value in scope.values()):
        raise ProbeFailure('staging_namespace_not_empty')
    deltas = {scope: {field: clean_after['totals'][scope][field]-value for field, value in values.items()}
              for scope, values in clean_before['totals'].items()}
    if any(deltas['visitor'].values()) or any(deltas['synthetic'].get(key) != value for key, value in EXPECTED.items()):
        raise ProbeFailure('isolated_aggregate_mismatch')
    cost = clean_after['cost'].get('estimated_usd_for_recorded_usage', {})
    if cost.get('synthetic') is None or Decimal(cost['synthetic']) != Decimal('0.00098') or Decimal(cost.get('visitor', '-1')) != 0:
        raise ProbeFailure('fixture_cost_arithmetic_mismatch')
    return {'cohort_deltas': deltas, 'fixture_estimate_usd': cost,
            'rates_are_fixture_arithmetic_only': True, 'actual_paid_provider_requests': 0,
            'actual_paid_provider_spend_usd': '0'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--control-file', type=Path, required=True)
    parser.add_argument('--expected-source-sha', required=True)
    parser.add_argument('--expected-run-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.live:
        parser.error('No requests sent. --live is required.')
    try:
        parsed = urlparse(args.base_url)
        port = parsed.port
    except ValueError:
        parser.error('Base URL must be an immutable HTTPS Vercel preview deployment origin.')
    # Reject the production alias and project/branch aliases; require an immutable Vercel preview URL.
    if (parsed.scheme != 'https' or parsed.username or parsed.password or port or parsed.path or parsed.query or parsed.fragment
            or not re.fullmatch(r'[a-z0-9-]+-[a-z0-9]{8,12}-[a-z0-9-]+\.vercel\.app', parsed.hostname or '')):
        parser.error('Base URL must be an immutable HTTPS Vercel preview deployment origin.')
    if not re.fullmatch(r'[a-f0-9]{40}', args.expected_source_sha) or not re.fullmatch(r'[a-f0-9]{12,32}', args.expected_run_id):
        parser.error('An exact source SHA and isolated run ID are required.')
    if args.output.suffix != '.json' or args.output.exists() or not args.output.parent.is_dir():
        parser.error('Output must be a new JSON file in an existing directory.')
    try:
        source = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        changed = subprocess.check_output(['git', 'diff', 'HEAD', '--name-only', '--', 'web', 'operations/hosted_workload.py'], cwd=ROOT, text=True).strip()
        untracked = subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard', '--', 'web', 'operations/hosted_workload.py'], cwd=ROOT, text=True).strip()
        if source != args.expected_source_sha or changed or untracked:
            parser.error('Local workload source must be clean at the expected committed SHA.')
        controls = {}
        for line in args.control_file.read_text(encoding='utf-8').splitlines():
            if line.strip() and not line.lstrip().startswith('#'):
                key, value = line.split('=', 1)
                if key.strip() not in {'STAGING_WORKLOAD_TOKEN', 'OPS_READ_TOKEN', 'VERCEL_AUTOMATION_BYPASS_SECRET'}:
                    parser.error('Control file contains an unsupported variable.')
                controls[key.strip()] = value.strip()
        if any(not 32 <= len(controls.get(name, '')) <= 512 for name in ('STAGING_WORKLOAD_TOKEN', 'OPS_READ_TOKEN')) or controls['STAGING_WORKLOAD_TOKEN'] == controls['OPS_READ_TOKEN']:
            parser.error('Distinct private staging and operations tokens are required.')
        hashes = {name: hashlib.sha256((ROOT/'web'/name).read_bytes()).hexdigest() for name in SOURCE_FILES}
        manifest = json.loads((ROOT/'web/model_manifest.json').read_text(encoding='utf-8'))
        artifacts = {name: info['sha256'] for name, info in manifest['files'].items()}
    except (OSError, ValueError, subprocess.SubprocessError):
        parser.error('Private controls or committed source could not be validated; details withheld.')
    report = {'schema_version': 1, 'started_at_utc': utc_now(), 'target_host': parsed.hostname,
              'source_sha': source, 'run_id': args.expected_run_id, 'source_sha256': hashes,
              'probe_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'scope': 'Bounded hosted Vercel FastAPI/Redis/ONNX diagnostic with deterministic injected provider; zero real model requests.',
              'limitations': 'No real provider latency/quality, scale/SLO, visitor outcome or production uptime claim. Client network timings include edge/runtime/retrieval/Redis and injected delay. Cold status is not established.',
              'bounds': {'query_requests': 16, 'auxiliary_gets': 6, 'max_concurrency': 8, 'daily_admissions': 9,
                         'paid_provider_requests': 0, 'response_bytes': 131072, 'socket_timeout_seconds': 125},
              'requests': [], 'auxiliary_requests': [], 'waits': [], 'passed': False}
    with args.output.open('x', encoding='utf-8') as output:
        output.write(json.dumps(report, indent=2)+'\n')
    def persist():
        args.output.write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    runner = Runner(args.base_url, controls, report, persist)
    try:
        provenance = runner.read('/api/staging/provenance')
        report['provenance'] = validate_provenance(provenance, source, args.expected_run_id, hashes, artifacts, parsed.hostname)
        health = runner.read('/api/health')
        if health.get('status') != 'ready' or health.get('model') != 'deterministic-fixture-v1':
            raise ProbeFailure('health_not_ready')
        before = runner.read('/api/operations?days=2')
        if any(value for scope in safe_snapshot(before)['totals'].values() for value in scope.values()):
            raise ProbeFailure('staging_namespace_not_empty')
        run_phases(runner)
        after = runner.read('/api/operations?days=2')
        report['accounting'] = verify_aggregates(before, after)
        report['passed'] = len(report['requests']) == 16
    except ProbeFailure as error:
        report['failure'] = str(error)
    except Exception:
        report['failure'] = 'unexpected_probe_failure_details_withheld'
    finally:
        report['completed_at_utc'] = utc_now()
        persist()
    print(json.dumps({'passed': report['passed'], 'query_requests': len(report['requests']),
                      'auxiliary_gets': len(report['auxiliary_requests']), 'paid_provider_requests': 0}), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
