"""Offline retained workload evidence must fail on inconsistent raw claims."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / 'operations/results/hosted-staging-verified-config-2026-10-25.json'


class WorkloadEvidenceTests(unittest.TestCase):
    def verifier(self):
        self.assertIsNotNone(importlib.util.find_spec('operations.verify_workload'), 'Offline workload verifier is missing')
        from operations.verify_workload import validate_report
        return validate_report

    def data(self):
        return json.loads(REPORT.read_text(encoding='utf-8'))

    def test_retained_complete_report_recomputes_offline(self):
        result = self.verifier()(self.data())
        self.assertEqual(result['query_requests_verified'], 16)
        self.assertEqual(result['http_status_counts'], {'200': 6, '429': 7, '502': 2, '503': 1})
        self.assertEqual(result['auxiliary_requests_verified'], 4)
        self.assertEqual(result['actual_paid_provider_requests'], 0)
        self.assertFalse(result['independent_attestation'])

    def test_mutated_status_sequence_and_raw_timing_are_rejected(self):
        verify = self.verifier()
        for kind in ('status', 'sequence', 'negative', 'nonfinite', 'bool', 'timestamp', 'retry', 'labels', 'valid'):
            data = self.data()
            if kind == 'status': data['requests'][1]['http_status'] = 200
            elif kind == 'sequence': data['requests'][1]['sequence'] = 1
            elif kind == 'negative': data['requests'][0]['elapsed_ms'] = -1
            elif kind == 'nonfinite': data['requests'][0]['elapsed_ms'] = float('nan')
            elif kind == 'bool': data['requests'][0]['elapsed_ms'] = True
            elif kind == 'timestamp': data['requests'][0]['started_at_utc'] = 'yesterday'
            elif kind == 'retry': data['requests'][3].pop('retry_after_seconds')
            elif kind == 'labels': data['requests'][0]['retrieved_labels'][0] = '[Source: invented, Section: invented]'
            else: data['requests'][0]['result_valid'] = False
            with self.subTest(kind=kind), self.assertRaises(ValueError): verify(data)

    def test_mutated_accounting_cost_wait_and_provenance_are_rejected(self):
        verify = self.verifier()
        for kind in ('count', 'visitor', 'cost', 'paid', 'wait', 'bound', 'prefix', 'runtime', 'probe', 'source'):
            data = self.data()
            if kind == 'count': data['accounting']['cohort_deltas']['synthetic']['usage_missing'] = 0
            elif kind == 'visitor': data['accounting']['cohort_deltas']['visitor']['queries'] = 1
            elif kind == 'cost': data['accounting']['fixture_estimate_usd']['synthetic'] = '0'
            elif kind == 'paid': data['accounting']['actual_paid_provider_requests'] = 1
            elif kind == 'wait': data['waits'][0]['seconds'] = 1
            elif kind == 'bound': data['bounds']['max_concurrency'] = 9
            elif kind == 'prefix': data['provenance']['operations_prefix'] = 'docwise:operations:v1'
            elif kind == 'runtime': data['provenance']['runtime_versions']['numpy'] = 'invented'
            elif kind == 'probe': data['probe_sha256'] = '0'*64
            else:
                data['source_sha256']['app.py'] = '0'*64
                data['provenance']['source_sha256']['app.py'] = '0'*64
            with self.subTest(kind=kind), self.assertRaises(ValueError): verify(data)

    def test_failed_setup_reports_are_never_successful_workload_evidence(self):
        verify = self.verifier()
        for name in ('hosted-staging-2026-10-25.json', 'hosted-staging-authenticated-2026-10-25.json'):
            report = json.loads((REPORT.parent/name).read_text(encoding='utf-8'))
            self.assertIs(report['passed'], False)
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'complete successful'):
                verify(report)


if __name__ == '__main__':
    unittest.main()
