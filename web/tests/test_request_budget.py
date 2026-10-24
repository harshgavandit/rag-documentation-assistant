"""Paid evaluation attempts are reserved durably before any network request."""
import json
from pathlib import Path
import tempfile
import unittest

from evaluation.request_budget import RequestBudget


class RequestBudgetTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'budget.json'

    def test_failed_or_unfinished_requests_still_consume_budget_on_restart(self):
        budget = RequestBudget(self.path, authorization='test-approval', limit=2, output_cap=800)
        budget.reserve('first')  # Model/network failure after reservation must not refund.
        restarted = RequestBudget(self.path, authorization='test-approval', limit=2, output_cap=800)
        restarted.reserve('second')
        with self.assertRaisesRegex(ValueError, 'exhausted'):
            restarted.reserve('third')
        self.assertEqual(len(json.loads(self.path.read_text())['attempts']), 2)

    def test_same_case_cannot_be_paid_for_twice_or_approval_expanded(self):
        RequestBudget(self.path, authorization='test-approval', limit=2, output_cap=800).reserve('first')
        with self.assertRaisesRegex(ValueError, 'already'):
            RequestBudget(self.path, authorization='test-approval', limit=2, output_cap=800).reserve('first')
        for args in [dict(authorization='different', limit=2, output_cap=800),
                     dict(authorization='test-approval', limit=3, output_cap=800),
                     dict(authorization='test-approval', limit=2, output_cap=801)]:
            with self.assertRaises(ValueError):
                RequestBudget(self.path, **args)

    def test_hard_maximum_and_invalid_ledger_fail_closed(self):
        for limit, cap in [(25, 800), (0, 800), (24, 801), (24, 0), (True, 800)]:
            with self.assertRaises(ValueError):
                RequestBudget(self.path, authorization='test', limit=limit, output_cap=cap)
        self.path.write_text('{"attempts": "corrupt"}')
        with self.assertRaises(ValueError):
            RequestBudget(self.path, authorization='test', limit=24, output_cap=800)

    def test_exclusive_lock_blocks_overlapping_reservation(self):
        budget = RequestBudget(self.path, authorization='test', limit=24, output_cap=800)
        self.path.with_suffix('.json.lock').write_text('')
        with self.assertRaises(FileExistsError):
            budget.reserve('first')
        self.assertEqual(json.loads(self.path.read_text())['attempts'], [])


if __name__ == '__main__':
    unittest.main()
