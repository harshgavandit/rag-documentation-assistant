"""Bounded hosted probe fails safely and retains honest per-request evidence."""
from contextlib import redirect_stderr
from io import StringIO
import json
from pathlib import Path
import threading
import tempfile
import unittest
from unittest.mock import patch


class HostedWorkloadTests(unittest.TestCase):
    def module(self):
        import importlib
        return importlib.import_module('operations.hosted_workload')

    def test_opt_in_origin_and_output_validation_precede_control_read_or_network(self):
        workload = self.module()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'existing.json'
            output.write_text('preserve')
            args = ['--base-url', 'https://rag-documentation-assistant.vercel.app', '--control-file', 'absent',
                    '--expected-source-sha', 'a'*40, '--expected-run-id', '0123456789abcdef', '--output', str(output)]
            with patch.object(workload, 'http', side_effect=AssertionError('network reached')), redirect_stderr(StringIO()):
                for selected in (args, ['--live', *args]):
                    with self.assertRaises(SystemExit) as caught:
                        workload.main(selected)
                    self.assertEqual(caught.exception.code, 2)
            self.assertEqual(output.read_text(), 'preserve')

    def test_malformed_origin_is_a_safe_validation_error(self):
        workload = self.module()
        with patch.object(workload, 'http', side_effect=AssertionError('network reached')), redirect_stderr(StringIO()):
            for origin in ('https://preview.vercel.app:broken', 'https://[broken'):
                with self.assertRaises(SystemExit) as caught:
                    workload.main(['--live', '--base-url', origin, '--control-file', 'absent',
                                   '--expected-source-sha', 'a'*40, '--expected-run-id', '0123456789abcdef', '--output', 'absent.json'])
                self.assertEqual(caught.exception.code, 2)

    def test_raw_timings_and_hard_query_cap_survive_rejections(self):
        workload = self.module()
        report = {'requests': [], 'auxiliary_requests': [], 'waits': []}
        def reject(*args, **kwargs):
            return {'http_status': 429, 'elapsed_ms': 12.5, 'retry_after_seconds': 11,
                    'started_at_utc': '2026-10-24T00:00:00+00:00', 'completed_at_utc': '2026-10-24T00:00:00.0125+00:00'}, None
        runner = workload.Runner('https://docwise-unique-abc123.vercel.app',
                                 {'STAGING_WORKLOAD_TOKEN': 's'*40, 'OPS_READ_TOKEN': 'o'*40},
                                 report, lambda: None, request=reject)
        for _ in range(16):
            runner.query('valid', 'bounded')
        with self.assertRaises(workload.ProbeFailure):
            runner.query('valid', 'over-bound')
        self.assertEqual(len(report['requests']), 16)
        self.assertEqual(report['requests'][0]['elapsed_ms'], 12.5)
        self.assertEqual(report['requests'][0]['retry_after_seconds'], 11)
        self.assertNotIn('s'*40, json.dumps(report))

    def test_wait_uses_returned_ttl_and_rejects_unbounded_values(self):
        workload = self.module()
        report = {'requests': [], 'auxiliary_requests': [], 'waits': []}
        waits = []
        runner = workload.Runner('https://docwise-unique-abc123.vercel.app', {}, report, lambda: None, sleeper=waits.append)
        runner.wait_window([{'retry_after_seconds': 2}])
        self.assertAlmostEqual(sum(waits), 2.25)
        for rows in ([{}], [{'retry_after_seconds': 86400}], [{'client_ttl_ms': -1}]):
            with self.assertRaises(workload.ProbeFailure):
                runner.wait_window(rows)

    def test_provenance_requires_exact_local_source_hashes_and_isolated_limits(self):
        workload = self.module()
        valid = {'schema_version': 1, 'source_sha': 'a'*40, 'run_id': '0123456789abcdef',
                 'environment': 'preview', 'provider_mode': 'deterministic-injected-no-network',
                 'provider_requests': 0, 'model': 'deterministic-fixture-v1',
                 'source_sha256': {'app.py': 'b'*64}, 'embedding_artifacts_sha256': {'model.onnx': 'c'*64},
                 'rate_limit_prefix': 'docwise:staging:0123456789abcdef:admission',
                 'operations_prefix': 'docwise:staging:0123456789abcdef:operations',
                 'limits': {'global_per_minute': 20, 'client_per_minute': 3, 'daily_admissions': 9},
                 'fixture_usage': {'prompt_tokens': 100, 'completion_tokens': 20},
                 'fixture_rates_usd_per_million': {'prompt': '1', 'output': '2'}}
        workload.validate_provenance(valid, 'a'*40, '0123456789abcdef', {'app.py': 'b'*64}, {'model.onnx': 'c'*64})
        for key, value in [('source_sha', 'd'*40), ('operations_prefix', 'docwise:operations:v1'),
                           ('source_sha256', {'app.py': 'e'*64}), ('provider_requests', 1),
                           ('limits', {'global_per_minute': 20, 'client_per_minute': 100, 'daily_admissions': 9})]:
            with self.assertRaises(workload.ProbeFailure):
                workload.validate_provenance(dict(valid, **{key: value}), 'a'*40, '0123456789abcdef', {'app.py': 'b'*64}, {'model.onnx': 'c'*64})
        runtime = {name: '1.2.3' for name in ('python', 'fastapi', 'numpy', 'onnxruntime', 'tokenizers', 'rank-bm25')}
        deployed = dict(valid, deployment_host='docwise-abc123456-testteam.vercel.app',
                        deployment_id='dpl_abcdefghijklmnop', runtime_versions=runtime)
        clean = workload.validate_provenance(deployed, 'a'*40, '0123456789abcdef', {'app.py': 'b'*64}, {'model.onnx': 'c'*64}, 'docwise-abc123456-testteam.vercel.app')
        self.assertEqual(clean['deployment_id'], 'dpl_abcdefghijklmnop')
        self.assertEqual(clean['runtime_versions']['python'], '1.2.3')
        for key, value in [('deployment_host', 'production.vercel.app'), ('deployment_id', 'untrusted'),
                           ('runtime_versions', dict(runtime, python='unbounded text or private value'))]:
            with self.assertRaises(workload.ProbeFailure):
                workload.validate_provenance(dict(deployed, **{key: value}), 'a'*40, '0123456789abcdef', {'app.py': 'b'*64}, {'model.onnx': 'c'*64}, 'docwise-abc123456-testteam.vercel.app')

    def test_all_sixteen_requests_follow_real_window_protocol_and_accounting(self):
        workload = self.module()
        report = {'requests': [], 'auxiliary_requests': [], 'waits': []}
        requests, waits = [], []
        counts = {'queries': 0, 'answered': 0, 'refused': 0, 'errors': 0, 'status_2xx': 0,
                  'status_4xx': 0, 'status_5xx': 0, 'provider_responses': 0, 'usage_complete': 0,
                  'usage_missing': 0, 'prompt_tokens': 0, 'output_tokens': 0,
                  'feedback_issued': 0, 'feedback_total': 0, 'latency_le_300000_ms': 0}
        lock = threading.Lock()
        in_phase = 0
        phase = 0
        source_rows = [{'document': 'a.md', 'section': 'Tools', 'rank': index} for index in range(1, 6)]
        def fake_request(base, path, headers, payload=None):
            nonlocal in_phase
            self.assertEqual(path, '/api/query')
            self.assertEqual(headers['x-staging-token'], 's'*40)
            self.assertEqual(headers['x-ops-token'], 'o'*40)
            self.assertEqual(payload['question'], 'What is FastMCP and how are tools defined?')
            scenario = headers['x-staging-scenario']
            with lock:
                in_phase += 1
                requests.append((phase, scenario))
                admitted = phase < 3 and in_phase <= 3
                status = (503 if scenario == 'provider_error' else 502 if scenario in ('invalid_citation', 'truncated') else 200) if admitted else 429
                counts['queries'] += 1
                counts['latency_le_300000_ms'] += 1
                counts['status_2xx' if status == 200 else 'status_4xx' if status == 429 else 'status_5xx'] += 1
                counts['refused' if status == 200 and scenario == 'refusal' else 'answered' if status == 200 else 'errors'] += 1
                if admitted and scenario != 'provider_error':
                    counts['provider_responses'] += 1
                    if scenario == 'missing_usage':
                        counts['usage_missing'] += 1
                    else:
                        counts['usage_complete'] += 1
                        counts['prompt_tokens'] += 100
                        counts['output_tokens'] += 20
                if status == 200 and scenario != 'refusal':
                    counts['feedback_issued'] += 1
                sequence = len(requests)
            meta = {'http_status': status, 'elapsed_ms': sequence+0.125,
                    'started_at_utc': '2026-10-24T00:00:00+00:00', 'completed_at_utc': '2026-10-24T00:00:00.010+00:00'}
            if status == 429:
                meta['retry_after_seconds'] = 86000 if phase == 3 else 59
            body = {'model': 'deterministic-fixture-v1', 'sources': source_rows, 'elapsed_seconds': .01,
                    'answer': workload.REFUSAL if scenario == 'refusal' else 'Fixture [Source: a.md, Section: Tools]',
                    'citations': [] if scenario == 'refusal' else ['[Source: a.md, Section: Tools]'],
                    'refused': scenario == 'refusal', 'staging_admission': {'client_ttl_ms': 59000}} if status == 200 else None
            return meta, body
        def advance_window(seconds):
            nonlocal phase, in_phase
            waits.append(seconds)
            # Runner divides waits into bounded chunks; expiry follows the final fraction.
            if seconds < 30:
                phase += 1
                in_phase = 0
        runner = workload.Runner('https://docwise-abc123456-testteam.vercel.app',
                                 {'STAGING_WORKLOAD_TOKEN': 's'*40, 'OPS_READ_TOKEN': 'o'*40},
                                 report, lambda: None, request=fake_request, sleeper=advance_window)
        workload.run_phases(runner)
        self.assertEqual(requests[:7], [(0, 'valid'), (0, 'invalid_citation'), (0, 'provider_error'), (0, 'valid'),
                                      (1, 'missing_usage'), (1, 'truncated'), (1, 'refusal')])
        self.assertEqual(requests[7:15], [(2, 'slow')]*8)
        self.assertEqual(requests[-1], (3, 'valid'))
        self.assertEqual(len(report['requests']), 16)
        self.assertEqual(len(report['waits']), 3)
        self.assertTrue(all(value <= 30 for value in waits))
        self.assertAlmostEqual(sum(waits), 177.75)
        self.assertEqual(sorted(row['http_status'] for row in report['requests'][7:15]), [200]*3+[429]*5)
        def snapshot(values, estimate):
            return {'schema_version': 1, 'window_days': 2, 'daily': [], 'totals': {'visitor': {}, 'synthetic': values},
                    'cost': {'rates_usd_per_million': {'prompt': '1', 'output': '2'},
                             'estimated_usd_for_recorded_usage': {'visitor': '0', 'synthetic': estimate}}}
        accounting = workload.verify_aggregates(snapshot({}, '0'), snapshot(counts, '0.00098'))
        self.assertEqual(accounting['cohort_deltas']['synthetic']['queries'], 16)
        self.assertEqual(accounting['cohort_deltas']['synthetic']['usage_missing'], 1)
        self.assertEqual(accounting['actual_paid_provider_requests'], 0)
        with self.assertRaises(workload.ProbeFailure):
            workload.verify_aggregates(snapshot({}, '0'), snapshot(dict(counts, usage_missing=0), '0.00098'))
        self.assertNotIn('s'*40, json.dumps(report))
        self.assertNotIn('o'*40, json.dumps(report))
