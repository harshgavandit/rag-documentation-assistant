"""Live evaluator's safety/accounting contracts; no provider calls or model load."""
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
import tempfile
from unittest.mock import patch
from evaluation.evaluate_answers import main, score_response, summarize, usage_fields, validate_budget


class AnswerEvaluationTests(unittest.TestCase):
    def test_budget_is_bounded(self):
        for bad in (0, -1, 31):
            with self.assertRaises(ValueError):
                validate_budget(bad)
        self.assertEqual(validate_budget(18), 18)

    def test_bad_output_stops_before_dataset_or_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            existing = Path(directory) / 'existing.json'
            existing.write_text('preserve')
            for path in (existing, Path(directory) / 'bad.txt', Path(directory) / 'missing' / 'new.json'):
                with self.subTest(path=path), patch('evaluation.evaluate_answers.load_dataset') as dataset, redirect_stderr(StringIO()):
                    with self.assertRaises(SystemExit) as caught:
                        main(['--live', '--output', str(path)])
                    self.assertEqual(caught.exception.code, 2)
                    dataset.assert_not_called()
            self.assertEqual(existing.read_text(), 'preserve')

    def test_cli_opt_in_and_budget_checks_precede_provider_import(self):
        examples = [(['--output', 'unused.json'], None),
                    (['--live', '--max-requests', '31', '--output', 'unused.json'], None),
                    (['--live', '--max-requests', '1', '--output', 'unused.json'], {'cases': [{'live_sample': True}, {'live_sample': True}]})]
        for args, dataset in examples:
            with self.subTest(args=args), patch('evaluation.evaluate_answers.load_dataset', return_value=(dataset, [])), patch('evaluation.evaluate_answers.importlib.import_module') as provider, redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    main(args)
                self.assertEqual(caught.exception.code, 2)
                provider.assert_not_called()

    def test_missing_fact_patterns_stop_before_provider_import(self):
        dataset = {'cases': [{'live_sample': True, 'answerable': True}]}
        with patch('evaluation.evaluate_answers.load_dataset', return_value=(dataset, [])), patch('evaluation.evaluate_answers.importlib.import_module') as provider, redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as caught:
                main(['--live', '--output', 'unused.json'])
            self.assertEqual(caught.exception.code, 2)
            provider.assert_not_called()

    def test_supported_fact_and_citation_checks(self):
        case = {'answerable': True, 'required_fact_patterns': ['FastMCP', 'tool']}
        chunks = [{'metadata': {'doc_title': 'guide.md', 'section_title': 'Server'}}]
        good = {'answer': 'FastMCP registers a tool.', 'refused': False, 'citations': ['[Source: guide.md, Section: Server]']}
        self.assertTrue(score_response(case, good, chunks)['fixture_checks_pass'])
        self.assertFalse(score_response(case, dict(good, citations=['[Source: other.md, Section: Server]']), chunks)['fixture_checks_pass'])
        self.assertFalse(score_response(case, dict(good, answer='Unrelated.'), chunks)['fixture_checks_pass'])

    def test_unsupported_must_refuse(self):
        case = {'answerable': False, 'required_fact_patterns': []}
        self.assertTrue(score_response(case, {'answer': 'Refusal', 'refused': True, 'citations': []}, [])['fixture_checks_pass'])
        self.assertFalse(score_response(case, {'answer': 'A guess', 'refused': False, 'citations': []}, [])['fixture_checks_pass'])

    def test_usage_whitelist_and_missing_values(self):
        data = usage_fields({'usage': {'prompt_tokens': 5, 'completion_tokens': 3, 'total_tokens': 8, 'secret': 'never copied'}})
        self.assertEqual(data['total_tokens'], 8)
        self.assertNotIn('secret', data)
        self.assertIsNone(usage_fields({'usage': {'prompt_tokens': True}})['prompt_tokens'])
        self.assertIsNone(usage_fields({})['total_tokens'])

    def test_errors_remain_in_denominator(self):
        row = {'status': 'completed', 'checks': {'expected_answerable': False, 'fixture_checks_pass': True, 'refused': True}}
        result = summarize([row, {'status': 'error'}])
        self.assertEqual(result['attempted_cases'], 2)
        self.assertEqual(result['completed_cases'], 1)
        self.assertEqual(result['error_cases'], 1)
        self.assertEqual(result['unsupported_refusals'], 1)

    def test_missing_usage_is_unknown_instead_of_measured_zero(self):
        unknown = {'status': 'error', 'usage': usage_fields({})}
        result = summarize([unknown])
        self.assertEqual(result['records_with_usage'], 0)
        self.assertEqual(result['records_with_complete_usage'], 0)
        self.assertIsNone(result['token_totals']['total_tokens'])
        known = {'status': 'error', 'usage': usage_fields({'usage': {'prompt_tokens': 5, 'completion_tokens': 3, 'total_tokens': 8}})}
        result = summarize([known, unknown])
        self.assertEqual(result['records_with_usage'], 1)
        self.assertEqual(result['known_token_subtotals']['total_tokens'], 8)
        self.assertIsNone(result['token_totals']['total_tokens'])
        self.assertEqual(summarize([known])['token_totals']['total_tokens'], 8)


if __name__ == '__main__':
    unittest.main()
