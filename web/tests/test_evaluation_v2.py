"""V2 dataset coverage and CLI/report compatibility checks without model calls."""
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from evaluation.metrics import ranking_metrics
from evaluation.run import (DATASET, CORPUS, ROOT, dataset_identity, load_dataset,
                            main, retain_sources, summarize_results)

V2 = ROOT / 'evaluation/cases_v2.json'


class V2DatasetTests(unittest.TestCase):
    def test_frozen_intent_inventory_is_balanced_and_excerpt_grounded(self):
        dataset, corpus = load_dataset(V2)
        cases = dataset['cases']
        self.assertEqual(len(cases), 112)
        self.assertEqual(len({case['intent_id'] for case in cases}), 112)
        self.assertEqual(len({case['question'].casefold() for case in cases}), 112)
        self.assertEqual(sum(case['answerable'] for case in cases), 96)
        for tag in ('maps', 'healthcare'):
            self.assertEqual(sum(tag in case['tags'] for case in cases), 48)
        for tag in ('ambiguous', 'multi_section', 'interpretive', 'implementation'):
            self.assertTrue(any(tag in case['tags'] for case in cases), tag)
        self.assertTrue(all(case['unsupported_reason'] for case in cases if not case['answerable']))
        self.assertEqual(dataset['freeze']['status'], 'frozen_before_v2_retrieval')
        self.assertEqual(len(corpus), 39)

    def test_v1_default_is_preserved_and_identity_uses_selected_dataset_bytes(self):
        original, _ = load_dataset()
        expanded, _ = load_dataset(V2)
        self.assertEqual(original['dataset_id'], 'docwise-authored-retrieval-v1')
        self.assertEqual(len(original['cases']), 50)
        self.assertEqual(dataset_identity(expanded, V2)['sha256'], hashlib.sha256(V2.read_bytes()).hexdigest())
        self.assertNotEqual(dataset_identity(expanded, V2)['sha256'], hashlib.sha256(DATASET.read_bytes()).hexdigest())

    def test_duplicate_question_rejected_even_if_id_differs(self):
        dataset, _ = load_dataset(V2)
        duplicate = deepcopy(dataset)
        duplicate['cases'][1]['question'] = '  ' + duplicate['cases'][0]['question'].upper() + '  '
        with tempfile.TemporaryDirectory(dir=DATASET.parent) as folder:
            path = Path(folder) / 'cases.json'
            path.write_text(json.dumps(duplicate), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Duplicate question'):
                load_dataset(path, CORPUS)

    def test_cli_validate_only_selects_v2_without_constructing_model(self):
        capture = io.StringIO()
        with patch('evaluation.run.evaluate') as evaluate, redirect_stdout(capture):
            code = main(['--dataset', str(V2), '--validate-only'])
        self.assertEqual(code, 0)
        evaluate.assert_not_called()
        result = json.loads(capture.getvalue())
        self.assertEqual(result['cases'], 112)
        self.assertEqual(result['dataset_id'], 'docwise-authored-retrieval-v2')

    def test_cli_passes_selected_dataset_to_evaluator_and_retains_sources(self):
        with tempfile.TemporaryDirectory(dir=DATASET.parent) as folder:
            output = Path(folder) / 'report.json'
            with patch('evaluation.run.evaluate', return_value={'summary': {}, 'source': {}}) as evaluate, \
                    patch('evaluation.run.retain_sources') as retain, redirect_stdout(io.StringIO()):
                self.assertEqual(main(['--dataset', str(V2), '--output', str(output)]), 0)
            self.assertEqual(evaluate.call_args.kwargs['dataset_path'], V2)
            retain.assert_called_once()
            self.assertEqual(retain.call_args.args[1], V2)
            self.assertEqual(json.loads(output.read_text(encoding='utf-8'))['summary'], {})

    def test_exact_source_snapshots_preserve_bytes_and_validate_portably(self):
        with tempfile.TemporaryDirectory(dir=DATASET.parent) as folder:
            output = Path(folder) / 'retrieval-v2.json'
            report = {'source': {}}
            retain_sources(report, V2, output)
            snapshots = report['source']['exact_byte_snapshots']
            self.assertEqual(set(snapshots), {'dataset', 'evaluator', 'metrics', 'engine', 'model_manifest', 'corpus'})
            self.assertEqual((output.parent / snapshots['dataset']['path_relative_to_report']).read_bytes(), V2.read_bytes())
            for snapshot in snapshots.values():
                path = output.parent / snapshot['path_relative_to_report']
                self.assertFalse(Path(snapshot['path_relative_to_report']).is_absolute())
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), snapshot['sha256'])


class BreakdownTests(unittest.TestCase):
    def row(self, case_id, answerable, ranking, gold):
        return {'case_id': case_id, 'answerable': answerable,
                'methods': {'hybrid': {'ranked_chunk_ids': ranking, 'warm_latency_samples_ms': [1, 2],
                    'metrics': ranking_metrics(ranking, gold) if answerable else None}}}

    def test_group_summary_uses_answerable_macro_mean_and_lists_every_complete_miss(self):
        rows = [self.row('answer-hit', True, ['a'], ['a', 'b']),
                self.row('answer-miss', True, ['wrong'], ['a']),
                self.row('unsupported', False, ['wrong'], [])]
        result = summarize_results(rows, 'hybrid')
        self.assertEqual(result['case_count'], 3)
        self.assertEqual(result['answerable_case_count'], 2)
        self.assertEqual(result['metrics']['hit_at_5'], .5)
        self.assertEqual(result['metrics']['recall_at_5'], .25)
        self.assertEqual(result['miss_at_5_case_ids'], ['answer-miss'])
        self.assertEqual(result['unsupported_cases']['count'], 1)
        self.assertEqual(result['unsupported_cases']['returned_nonempty_count'], 1)
        self.assertEqual(result['warm_retrieval_latency_all_cases']['sample_count'], 6)

    def test_unsupported_only_group_has_null_ranking_metrics(self):
        result = summarize_results([self.row('unsupported', False, ['a'], [])], 'hybrid')
        self.assertIsNone(result['metrics'])
        self.assertEqual(result['answerable_case_count'], 0)
        self.assertEqual(result['miss_at_5_case_ids'], [])


if __name__ == '__main__':
    unittest.main()
