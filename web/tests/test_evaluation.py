"""Meaningful checks for metrics, frozen gold sources and stale-corpus rejection."""
from copy import deepcopy
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from evaluation.metrics import ranking_metrics, timing_summary
from evaluation.run import DATASET, CORPUS, chunk_id, load_dataset, make_retrievers


class MetricTests(unittest.TestCase):
    def test_hand_calculated_ranking(self):
        result = ranking_metrics(['irrelevant', 'b', 'c', 'a', 'other'], ['a', 'b', 'missing'])
        self.assertEqual(result['hit_at_1'], 0)
        self.assertEqual(result['hit_at_5'], 1)
        self.assertEqual(result['mrr_at_5'], .5)
        self.assertAlmostEqual(result['recall_at_5'], 2 / 3)
        expected_dcg = 1 / math.log2(3) + 1 / math.log2(5)
        expected_ideal = 1 + 1 / math.log2(3) + 1 / math.log2(4)
        self.assertAlmostEqual(result['ndcg_at_5'], expected_dcg / expected_ideal)

    def test_perfect_and_empty_and_beyond_cutoff(self):
        perfect = ranking_metrics(['a', 'b'], ['a', 'b'])
        self.assertTrue(all(value == 1 for value in perfect.values()))
        for ranking in ([], ['x', 'y', 'z', 'q', 'r', 'a']):
            self.assertTrue(all(value == 0 for value in ranking_metrics(ranking, ['a']).values()))

    def test_recall_uses_all_gold_even_when_more_than_cutoff(self):
        result = ranking_metrics(list('abcde'), list('abcdef'))
        self.assertAlmostEqual(result['recall_at_5'], 5 / 6)
        self.assertEqual(result['ndcg_at_5'], 1)

    def test_duplicate_rankings_and_empty_gold_rejected(self):
        for ranking, gold in ((['a', 'a'], ['a']), (['a'], [])):
            with self.assertRaises(ValueError):
                ranking_metrics(ranking, gold)

    def test_percentiles_interpolate_and_keep_raw_count(self):
        result = timing_summary([30, 0, 10, 20])
        self.assertEqual(result['sample_count'], 4)
        self.assertEqual(result['mean_ms'], 15)
        self.assertEqual(result['p50_ms'], 15)
        self.assertAlmostEqual(result['p95_ms'], 28.5)
        self.assertAlmostEqual(result['p99_ms'], 29.7)
        self.assertEqual(timing_summary([7])['p99_ms'], 7)
        for values in ([], [-1], [float('nan')], [float('inf')]):
            with self.assertRaises(ValueError):
                timing_summary(values)


class GoldSourceTests(unittest.TestCase):
    def setUp(self):
        self.dataset, self.corpus = load_dataset()

    def altered_dataset(self, dataset, corpus=None):
        with tempfile.TemporaryDirectory(dir=DATASET.parent) as folder:
            dataset_path = Path(folder) / 'cases.json'
            corpus_path = Path(folder) / 'corpus.json'
            corpus_path.write_bytes(CORPUS.read_bytes() if corpus is None else corpus)
            dataset_path.write_text(json.dumps(dataset), encoding='utf-8')
            return load_dataset(dataset_path, corpus_path)

    def test_every_gold_excerpt_matches_frozen_source(self):
        self.assertEqual(len(self.dataset['cases']), 50)
        self.assertEqual(sum(c['answerable'] for c in self.dataset['cases']), 44)
        ids = {chunk_id(c): c for c in self.corpus}
        docs = set()
        for case in self.dataset['cases']:
            for evidence in case['evidence']:
                source = ids[evidence['chunk_id']]
                self.assertIn(evidence['excerpt'], source['content'])
                docs.add(source['metadata']['doc_title'])
        self.assertEqual(docs, {'DOCUMENTATION.md', 'MCP_Documentation.md'})
        selected = [c for c in self.dataset['cases'] if c['live_sample']]
        self.assertEqual(len(selected), 18)
        self.assertEqual(sum(c['answerable'] for c in selected), 12)

    def test_stale_corpus_is_rejected_before_retrieval(self):
        with self.assertRaisesRegex(ValueError, 'Stale corpus'):
            self.altered_dataset(self.dataset, CORPUS.read_bytes() + b' ')

    def test_unknown_id_duplicate_id_and_wrong_excerpt_are_rejected(self):
        for change in ('unknown', 'duplicate', 'excerpt', 'missing_evidence', 'unsupported'):
            dataset = deepcopy(self.dataset)
            if change == 'unknown':
                dataset['cases'][0]['relevant_chunk_ids'] = ['invented::999']
            elif change == 'duplicate':
                dataset['cases'][1]['id'] = dataset['cases'][0]['id']
            elif change == 'excerpt':
                dataset['cases'][0]['evidence'][0]['excerpt'] = 'invented source passage'
            elif change == 'missing_evidence':
                dataset['cases'][0]['evidence'] = []
            else:
                dataset['cases'][-1]['answerable'] = True
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.altered_dataset(dataset)

    def test_bm25_baseline_omits_nonpositive_scores_and_stably_breaks_ties(self):
        index = Mock()
        index.chunks = self.corpus[:5]
        index.bm25.get_scores.return_value = [0, 2, -1, 2, 1]
        retrievers = make_retrievers(index, Mock())
        self.assertEqual(retrievers['bm25']('Type_Hints QUERY!'),
                         [chunk_id(self.corpus[i]) for i in (1, 3, 4)])
        index.bm25.get_scores.assert_called_once_with(['type_hints', 'query'])

    def test_hybrid_adapter_delegates_to_unchanged_production_search(self):
        index = Mock()
        index.search.return_value = [self.corpus[2]]
        retrievers = make_retrievers(index, Mock())
        self.assertEqual(retrievers['hybrid']('question'), [chunk_id(self.corpus[2])])
        index.search.assert_called_once_with('question', top_k=5)


if __name__ == '__main__':
    unittest.main()
