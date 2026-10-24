"""Returned usage estimates do not turn missing data into zero-cost evidence."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from evaluation import broader_answers as live
from evaluation.broader_answers import checks, summarize
from evaluation.run import ROOT, load_dataset, sha256

REFUSAL = 'I could not find an answer to this question in the provided documentation.'


class BroaderAnswerTests(unittest.TestCase):
    def test_returned_usage_pricing_and_missing_usage_are_separate(self):
        row = {'status': 'completed', 'usage': {'prompt_tokens': 1000, 'completion_tokens': 100,
                'total_tokens': 1100, 'cached_prompt_tokens': 500},
               'checks': {'expected_refusal_matches': True, 'non_refusal_labels_valid': True,
                          'refused': False, 'expected_answerable': True}}
        summary = summarize([row, {'status': 'error'}])
        self.assertEqual(summary['records_with_complete_usage'], 1)
        self.assertEqual(summary['records_priced_from_returned_usage'], 1)
        self.assertAlmostEqual(summary['returned_usage_estimate_usd'], .000105)
        self.assertEqual(summary['human_evaluated_answers'], 0)
        self.assertEqual(summarize([{'status': 'error'}])['returned_usage_estimate_usd'], None)

    def test_label_and_expected_refusal_proxies_do_not_claim_semantic_correctness(self):
        chunks = [{'metadata': {'doc_title': 'd', 'section_title': 's', 'chunk_index': 0}}]
        result = checks({'answerable': True, 'relevant_chunk_ids': ['d::000']},
                        {'refused': False, 'citations': ['[Source: d, Section: s]']}, chunks)
        self.assertTrue(result['expected_refusal_matches'])
        self.assertTrue(result['non_refusal_labels_valid'])
        self.assertFalse(result['semantic_correctness_human_reviewed'])


class _BroaderAnswerReportFixture:
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)/'answers.json'
        (self.path.parent/'sources').mkdir()
        dataset, corpus = load_dataset(ROOT/'evaluation/broader_v1/heldout.json', ROOT/'evaluation/broader_v1/corpus.json')
        self.case = dataset['cases'][0]
        self.chunks = corpus[:5]
        self.label = f"[Source: {self.chunks[0]['metadata']['doc_title']}, Section: {self.chunks[0]['metadata']['section_title']}]"
        self.row = {'id': self.case['id'], 'question': self.case['question'], 'status': 'completed',
                    'retrieved_chunk_ids': [f"{c['metadata']['doc_title']}::{c['metadata']['chunk_index']:03}" for c in self.chunks],
                    'result': {'answer': 'A diagnostic fact. '+self.label, 'citations': [self.label], 'refused': False},
                    'provider_output': '  A diagnostic fact. '+self.label+'  ', 'finish_reason': 'stop',
                    'returned_model': 'gpt-6-luna',
                    'usage': {'prompt_tokens': 1000, 'completion_tokens': 100, 'total_tokens': 1100,
                              'cached_prompt_tokens': 500, 'reasoning_tokens': 0},
                    'provider_ms': 10., 'retrieval_ms': 1., 'elapsed_ms': 11.}
        self.row['checks'] = checks(self.case, self.row['result'], self.chunks)
        self.report = {'schema_version': 1, 'dataset_sha256': sha256(ROOT/'evaluation/broader_v1/heldout.json'),
                       'corpus_sha256': sha256(ROOT/'evaluation/broader_v1/corpus.json'), 'max_completion_tokens': 800,
                       'requested_model': 'gpt-6-luna', 'source_commit': 'a'*40, 'working_tree_dirty': False,
                       'request_limit': 24, 'cases': [self.row], 'provider_requests': 1,
                       'source_snapshots': {}, 'pricing': live.PRICING}
        for name in live.SOURCE_FILES:
            path = self.path.parent/'sources'/(name.replace('/', '_')+'.txt')
            path.write_bytes((ROOT/name).read_bytes())
            self.report['source_snapshots'][name] = {'path': path.relative_to(self.path.parent).as_posix(), 'sha256': sha256(path)}

    def save(self, report=None):
        selected = self.report if report is None else report
        selected['summary'] = summarize(selected['cases'])
        self.path.write_text(json.dumps(selected), encoding='utf-8')


class BroaderAnswerVerificationTests(_BroaderAnswerReportFixture, unittest.TestCase):
    def test_valid_report_recomputes_from_raw_provider_output_without_provider_calls(self):
        self.save()
        self.assertTrue(live.verify(self.path)['verified'])

    def test_mutated_answer_or_separate_citation_list_cannot_preserve_proxy_success(self):
        for field, value in [('answer', 'Uncited replacement'), ('citations', []), ('refused', True), ('refused', 0)]:
            changed = copy.deepcopy(self.report)
            changed['cases'][0]['result'][field] = value
            changed['cases'][0]['checks'] = checks(self.case, changed['cases'][0]['result'], self.chunks)
            self.save(changed)
            with self.assertRaises(ValueError, msg=field):
                live.verify(self.path)
        changed = copy.deepcopy(self.report)
        changed['cases'][0]['finish_reason'] = 'length'
        self.save(changed)
        with self.assertRaises(ValueError):
            live.verify(self.path)


class BroaderAnswerResumeTests(_BroaderAnswerReportFixture, unittest.TestCase):
    def prefix(self, count=10):
        dataset, _ = load_dataset(ROOT/'evaluation/broader_v1/heldout.json', ROOT/'evaluation/broader_v1/corpus.json')
        self.dataset = dataset
        self.report['cases'] = []
        for case in dataset['cases'][:count]:
            row = copy.deepcopy(self.row)
            row.update(id=case['id'], question=case['question'])
            row['checks'] = checks(case, row['result'], self.chunks)
            self.report['cases'].append(row)
        if count:
            self.report['cases'][-1] = {key: value for key, value in self.report['cases'][-1].items()
                                       if key not in {'result', 'checks', 'usage', 'provider_output', 'finish_reason', 'returned_model'}}
            self.report['cases'][-1].update(status='error', error_type='TimeoutError', http_status=None)
        self.report['provider_requests'] = count
        self.save()
        self.ledger = self.path.parent/'private-budget.json'
        budget = live.RequestBudget(self.ledger, authorization='user-approved-2026-10-25-24-additional', limit=24, output_cap=800)
        for case in dataset['cases'][:count]:
            budget.reserve(case['id'])
        return budget

    def test_resume_preflight_requires_exact_existing_ledger_and_preserves_prior_bytes(self):
        self.prefix()
        before = self.path.read_bytes()
        report, budget, start = live.prepare_resume(self.path, self.ledger)
        self.assertEqual(start, 10)
        self.assertEqual(report['cases'][-1]['error_type'], 'TimeoutError')
        self.assertEqual(self.path.read_bytes(), before)
        absent = self.path.parent/'absent-ledger.json'
        with self.assertRaises(ValueError):
            live.prepare_resume(self.path, absent)
        self.assertFalse(absent.exists())
        ledger = json.loads(self.ledger.read_text())
        for change in ('missing', 'wrong_case', 'authorization'):
            changed = copy.deepcopy(ledger)
            if change == 'missing':
                changed['attempts'].pop()
            elif change == 'wrong_case':
                changed['attempts'][-1]['case_id'] = 'different-case'
            else:
                changed['authorization'] = 'different-approval'
            self.ledger.write_text(json.dumps(changed))
            with self.assertRaises(ValueError, msg=change):
                live.prepare_resume(self.path, self.ledger)
        self.assertEqual(self.path.read_bytes(), before)

    def test_remaining_fourteen_calls_never_retry_timeout_and_cannot_replay_completed_budget(self):
        self.prefix()
        before = self.path.read_bytes()
        prior_rows = copy.deepcopy(self.report['cases'])
        archives = {name: (self.path.parent/entry['path']).read_bytes() for name, entry in self.report['source_snapshots'].items()}
        report, budget, start = live.prepare_resume(self.path, self.ledger)
        live.attach_continuation(self.path, report, budget)
        segment = report['continuation_segments'][0]
        self.assertEqual((self.path.parent/segment['before_report']['path']).read_bytes(), before)
        attempted = []
        site = SimpleNamespace()
        def provider(url, payload, token, timeout=15):
            attempted.append(payload['question'])
            return {'model': 'gpt-6-luna', 'choices': [{'finish_reason': 'stop', 'message': {'content': 'A diagnostic fact. '+self.label}}],
                    'usage': {'prompt_tokens': 100, 'completion_tokens': 10, 'total_tokens': 110,
                              'prompt_tokens_details': {'cached_tokens': 0}, 'completion_tokens_details': {'reasoning_tokens': 0}}}
        site.post_json = provider
        def answer(question, chunks):
            site.post_json('https://api.openai.com/v1/chat/completions', {'max_completion_tokens': 800, 'question': question}, 'offline-unused')
            return {'answer': 'A diagnostic fact. '+self.label, 'citations': [self.label], 'refused': False}
        site.answer = answer
        index = SimpleNamespace(search=lambda question: self.chunks)
        self.assertEqual(live.run_cases(site, index, self.dataset, report, budget, self.path, start), 0)
        self.assertEqual(attempted, [c['question'] for c in self.dataset['cases'][10:]])
        self.assertNotIn(self.dataset['cases'][9]['question'], attempted)
        self.assertEqual(report['cases'][:10], prior_rows)
        self.assertEqual(len(budget.snapshot()['attempts']), 24)
        self.assertTrue(live.verify(self.path)['complete_sample'])
        for name, entry in report['source_snapshots'].items():
            self.assertEqual((self.path.parent/entry['path']).read_bytes(), archives[name])
        with self.assertRaises(ValueError):
            live.prepare_resume(self.path, self.ledger)
        changed = copy.deepcopy(report)
        changed['cases'][0]['provider_ms'] += 1
        self.save(changed)
        with self.assertRaises(ValueError):
            live.verify(self.path)

    def test_next_real_network_failure_consumes_one_distinct_slot_and_stops(self):
        self.prefix()
        report, budget, start = live.prepare_resume(self.path, self.ledger)
        live.attach_continuation(self.path, report, budget)
        site = SimpleNamespace()
        def fail(*args, **kwargs):
            raise TimeoutError('offline timeout fixture')
        site.post_json = fail
        site.answer = lambda question, chunks: site.post_json('https://api.openai.com/v1/chat/completions', {'max_completion_tokens': 800}, 'offline-unused')
        self.assertEqual(live.run_cases(site, SimpleNamespace(search=lambda question: self.chunks), self.dataset, report, budget, self.path, start), 1)
        self.assertEqual(report['provider_requests'], 11)
        self.assertEqual(report['cases'][-1]['id'], self.dataset['cases'][10]['id'])
        self.assertEqual(len(budget.snapshot()['attempts']), 11)
        self.assertTrue(live.verify(self.path)['verified'])


class BroaderAnswerUsageArchiveTests(_BroaderAnswerReportFixture, unittest.TestCase):
    def test_quote_wrapped_exact_refusal_follows_production_normalization(self):
        changed = copy.deepcopy(self.report)
        changed['cases'][0].update(provider_output='  "'+REFUSAL+'"  ',
                                   result={'answer': REFUSAL, 'citations': [], 'refused': True})
        changed['cases'][0]['checks'] = checks(self.case, changed['cases'][0]['result'], self.chunks)
        self.save(changed)
        self.assertTrue(live.verify(self.path)['verified'])

    def test_negative_bool_inconsistent_and_over_cap_usage_cannot_be_priced_as_valid(self):
        mutations = [{'completion_tokens': -700, 'total_tokens': 300},
                     {'prompt_tokens': True, 'total_tokens': 101}, {'completion_tokens': 801, 'total_tokens': 1801},
                     {'cached_prompt_tokens': 1001}, {'total_tokens': 1}, {'reasoning_tokens': 101}]
        for mutation in mutations:
            changed = copy.deepcopy(self.report)
            changed['cases'][0]['usage'].update(mutation)
            self.save(changed)
            with self.assertRaises(ValueError, msg=str(mutation)):
                live.verify(self.path)

    def test_missing_usage_remains_unknown_without_fabricated_zero_cost(self):
        changed = copy.deepcopy(self.report)
        changed['cases'][0].pop('usage')
        self.save(changed)
        self.assertTrue(live.verify(self.path)['verified'])
        self.assertIsNone(changed['summary']['returned_usage_estimate_usd'])

    def test_falsy_non_object_usage_and_unbounded_token_integers_are_rejected(self):
        for invalid in (False, [], {'prompt_tokens': 10000001, 'completion_tokens': 100, 'total_tokens': 10000101,
                                   'cached_prompt_tokens': 500, 'reasoning_tokens': 0}):
            changed = copy.deepcopy(self.report)
            changed['cases'][0]['usage'] = invalid
            self.save(changed)
            with self.assertRaises(ValueError):
                live.verify(self.path)

    def test_missing_source_inventory_traversal_and_modified_snapshot_are_rejected(self):
        for mutation in ('empty', 'outside', 'changed'):
            changed = copy.deepcopy(self.report)
            if mutation == 'empty':
                changed['source_snapshots'] = {}
            elif mutation == 'outside':
                changed['source_snapshots']['web/app.py']['path'] = '../outside.txt'
            else:
                changed['source_snapshots']['web/app.py']['sha256'] = '0'*64
            self.save(changed)
            with self.assertRaises(ValueError, msg=mutation):
                live.verify(self.path)


if __name__ == '__main__':
    unittest.main()
