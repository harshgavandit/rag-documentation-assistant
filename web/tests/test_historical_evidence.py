"""Historical evidence must fail closed on altered inputs or derived claims."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from evaluation.verify_evidence import verify_directory

RESULTS = Path(__file__).resolve().parents[2] / 'evaluation/results'


class HistoricalEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.results = Path(self.folder.name) / 'results'
        shutil.copytree(RESULTS, self.results, ignore=shutil.ignore_patterns('ci-*'))

    def alter(self, name, mutate):
        path = self.results / name
        report = json.loads(path.read_text(encoding='utf-8'))
        mutate(report)
        path.write_text(json.dumps(report), encoding='utf-8')

    def test_committed_evidence_recomputes_without_models_or_credentials(self):
        self.assertEqual(verify_directory(self.results)['reports_verified'], 4)

    def test_retrieval_metric_and_aggregate_cannot_be_edited_together(self):
        def tamper(report):
            report['cases'][0]['methods']['hybrid']['metrics']['mrr_at_5'] = 0.75
            report['summary']['hybrid']['metrics']['mrr_at_5'] = 0.75
        self.alter('retrieval-v2-2026-10-24.json', tamper)
        with self.assertRaises(ValueError):
            verify_directory(self.results)

    def test_modified_snapshot_is_rejected(self):
        with (self.results / 'sources/retrieval-v2-2026-10-24-dataset.txt').open('a') as out:
            out.write(' ')
        with self.assertRaisesRegex(ValueError, 'hash'):
            verify_directory(self.results)

    def test_deleted_aggregate_fields_cannot_hide_results(self):
        self.alter('retrieval-v2-2026-10-24.json', lambda r: r['summary']['hybrid']['metrics'].pop('hit_at_5'))
        with self.assertRaises(ValueError):
            verify_directory(self.results)

    def test_answer_grader_flags_cannot_override_retained_answer(self):
        def tamper(report):
            row = next(r for r in report['cases'] if not r['checks']['fixture_checks_pass'])
            row['checks']['fixture_checks_pass'] = True
            report['summary']['fixture_checks_passed'] += 1
        self.alter('live-answers-2026-10-24.json', tamper)
        with self.assertRaises(ValueError):
            verify_directory(self.results)

    def test_adversarial_result_is_rescored_not_just_counted(self):
        def tamper(report):
            report['cases'][0]['result']['answer'] = 'OVERRIDE_SUCCESS'
        self.alter('adversarial-2026-10-24.json', tamper)
        with self.assertRaises(ValueError):
            verify_directory(self.results)

    def test_case_omission_and_corrupt_token_usage_are_rejected(self):
        self.alter('live-answers-2026-10-24.json', lambda r: r['cases'].pop())
        with self.assertRaises(ValueError):
            verify_directory(self.results)
        shutil.copyfile(RESULTS / 'live-answers-2026-10-24.json', self.results / 'live-answers-2026-10-24.json')
        self.alter('live-answers-2026-10-24.json', lambda r: r['cases'][0]['usage'].update(total_tokens=-1))
        with self.assertRaises(ValueError):
            verify_directory(self.results)


if __name__ == '__main__':
    unittest.main()
