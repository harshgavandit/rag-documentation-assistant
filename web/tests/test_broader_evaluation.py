"""Separate multi-domain corpus, frozen partitions and offline evidence contracts."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import importlib
import inspect
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from evaluation.metrics import ranking_metrics
from evaluation.run import (CORPUS, DATASET, METHODS, ROOT, dataset_identity, load_dataset,
                            main, retain_sources, sha256, summarize_results)
from web.engine import Index

BROADER = ROOT / 'evaluation/broader_v1'


class AlternateCorpusTests(unittest.TestCase):
    def test_engine_accepts_explicit_corpus_without_changing_default(self):
        self.assertIn('chunks', inspect.signature(Index).parameters)
        self.assertIsNone(inspect.signature(Index).parameters['chunks'].default)

    def test_cli_validates_selected_corpus_without_index_construction(self):
        with tempfile.TemporaryDirectory(dir=DATASET.parent) as directory:
            corpus = Path(directory) / 'corpus.json'
            corpus.write_bytes(CORPUS.read_bytes())
            output = io.StringIO()
            with patch('evaluation.run.evaluate') as evaluate, redirect_stdout(output), redirect_stderr(io.StringIO()):
                try:
                    code = main(['--dataset', str(DATASET), '--corpus', str(corpus), '--validate-only'])
                except SystemExit as error:
                    code = error.code
            self.assertEqual(code, 0)
            evaluate.assert_not_called()
            self.assertEqual(json.loads(output.getvalue())['chunks'], 39)

    def test_snapshot_uses_selected_corpus_bytes(self):
        self.assertIn('corpus_path', inspect.signature(retain_sources).parameters)
        with tempfile.TemporaryDirectory(dir=DATASET.parent) as directory:
            corpus = Path(directory) / 'alternate.json'
            corpus.write_bytes(b'[]\n')
            report = {'source': {}}
            output = Path(directory) / 'report.json'
            retain_sources(report, DATASET, output, corpus_path=corpus)
            entry = report['source']['exact_byte_snapshots']['corpus']
            self.assertEqual((output.parent / entry['path_relative_to_report']).read_bytes(), corpus.read_bytes())

    def test_explicit_index_encodes_only_supplied_content_and_default_keeps_39_chunks(self):
        supplied = [{'content': 'A distinct benchmark fact.', 'metadata': {'doc_title': 'test.md', 'chunk_index': 0}}]
        with patch('web.engine.ort.InferenceSession'), patch('web.engine.Tokenizer'), \
                patch.object(Index, 'encode', return_value=object()) as encode:
            explicit = Index(chunks=supplied)
            self.assertIs(explicit.chunks, supplied)
            encode.assert_called_once_with(['A distinct benchmark fact.'])
            self.assertEqual(len(Index().chunks), 39)


class BroaderBundleTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('evaluation.broader'), 'Separate bundle validator is missing')
        return importlib.import_module('evaluation.broader')

    def test_bundle_has_source_grounded_domain_disjoint_partitions(self):
        result = self.module().validate_bundle()
        self.assertEqual(result['case_count'], 60)
        self.assertEqual(result['answerable_case_count'], 50)
        self.assertEqual(result['partitions'], {'dev': 36, 'heldout': 24})
        self.assertEqual(result['source_document_count'], 6)
        self.assertEqual(result['domain_count'], 5)
        self.assertEqual(result['truncated_chunk_count'], 13)
        self.assertEqual(result['provider_requests_made'], 0)

    def test_source_byte_drift_and_split_overlap_are_rejected(self):
        module = self.module()
        files = module.bundle_bytes(BROADER)
        freeze = json.loads((BROADER / 'freeze.json').read_text(encoding='utf-8'))
        damaged = dict(files)
        damaged['sources/banking-README.md'] += b' '
        with self.assertRaisesRegex(ValueError, 'hash'):
            module.validate_contents(damaged, freeze)
        heldout = json.loads(files['heldout.json'])
        heldout['cases'][0]['intent_id'] = json.loads(files['dev.json'])['cases'][0]['intent_id']
        damaged = dict(files, **{'heldout.json': module.json_bytes(heldout)})
        refrozen = deepcopy(freeze)
        refrozen['files']['heldout.json'] = module.digest(damaged['heldout.json'])
        with self.assertRaisesRegex(ValueError, 'intent'):
            module.validate_contents(damaged, refrozen)

    def test_source_url_and_export_cannot_drift_even_after_rehash(self):
        module = self.module()
        files = module.bundle_bytes(BROADER)
        freeze = json.loads((BROADER / 'freeze.json').read_text(encoding='utf-8'))
        manifest = json.loads(files['manifest.json'])
        manifest['source_documents'][0]['source_url'] = 'https://github.com/harshgavandit/banking-agent-harness/blob/main/README.md'
        damaged = dict(files, **{'manifest.json': module.json_bytes(manifest)})
        refrozen = deepcopy(freeze)
        refrozen['files']['manifest.json'] = module.digest(damaged['manifest.json'])
        with self.assertRaisesRegex(ValueError, 'immutable'):
            module.validate_contents(damaged, refrozen)

    def test_pending_review_cannot_start_measurement(self):
        module = self.module()
        files = module.bundle_bytes(BROADER)
        freeze = json.loads((BROADER / 'freeze.json').read_text(encoding='utf-8'))
        freeze['source_review']['status'] = 'pending_independent_agent_review'
        with self.assertRaisesRegex(ValueError, 'review'):
            module.validate_contents(files, freeze, require_review=True)


class BroaderReportTests(unittest.TestCase):
    def synthetic_report(self):
        """Empty rankings and fixed timings are unit fixtures, never measurements."""
        dataset_path = BROADER / 'dev.json'
        dataset, _ = load_dataset(dataset_path, BROADER / 'corpus.json')
        rows = [{'case_id': case['id'], 'intent_id': case['intent_id'], 'question': case['question'],
                 'tags': case['tags'], 'answerable': case['answerable'], 'relevant_chunk_ids': case['relevant_chunk_ids'],
                 'methods': {method: {'ranked_chunk_ids': [], 'warm_latency_samples_ms': [1, 2],
                     'metrics': ranking_metrics([], case['relevant_chunk_ids']) if case['answerable'] else None,
                     'missed_relevant_chunk_ids': case['relevant_chunk_ids']} for method in METHODS}}
                for case in dataset['cases']]
        manifest = json.loads((ROOT / 'web/model_manifest.json').read_text(encoding='utf-8'))
        source = {name + '_sha256': sha256(ROOT / path) for name, path in
                  [('engine', 'web/engine.py'), ('evaluator', 'evaluation/run.py'), ('metrics', 'evaluation/metrics.py')]}
        return {'schema_version': 1, 'evaluation_kind': 'synthetic_unit_test_fixture',
                'retrieval_scope': 'separate_frozen_diagnostic_corpus',
                'dataset': dataset_identity(dataset, dataset_path), 'corpus': dataset['corpus'], 'source': source,
                'model': {'model': manifest['model'], 'revision': manifest['revision'],
                    'manifest_sha256': sha256(ROOT / 'web/model_manifest.json'),
                    'artifact_sha256': {name: info['sha256'] for name, info in manifest['files'].items()}},
                'protocol': {'top_k': 5, 'warm_repeats_per_case': 2}, 'cases': rows,
                'summary': {method: summarize_results(rows, method) for method in METHODS},
                'by_tag': {tag: {method: summarize_results([row for row in rows if tag in row['tags']], method)
                    for method in METHODS} for tag in {tag for row in rows for tag in row['tags']}}}

    def test_report_verifier_rejects_tampered_metrics_and_snapshot_bytes(self):
        module = importlib.import_module('evaluation.broader')
        with tempfile.TemporaryDirectory(dir=DATASET.parent) as directory:
            output = Path(directory) / 'synthetic-unit-fixture.json'
            report = self.synthetic_report()
            retain_sources(report, BROADER / 'dev.json', output, BROADER / 'corpus.json')
            module.retain_bundle(report, output)
            output.write_bytes(module.json_bytes(report))
            self.assertEqual(module.verify_report(output)['retrieval_cases_verified'], 36)
            report['cases'][0]['methods']['hybrid']['metrics']['hit_at_1'] = 1
            output.write_bytes(module.json_bytes(report))
            with self.assertRaises(ValueError):
                module.verify_report(output)
            report['cases'][0]['methods']['hybrid']['metrics']['hit_at_1'] = 0
            output.write_bytes(module.json_bytes(report))
            entry = report['broader_evidence']['exact_byte_snapshots']['sources/cache-README.md']
            (output.parent / entry['path_relative_to_report']).write_bytes(b'changed snapshot')
            with self.assertRaisesRegex(ValueError, 'hash'):
                module.verify_report(output)

    def test_existing_output_stops_before_local_model_work(self):
        module = importlib.import_module('evaluation.broader')
        with tempfile.TemporaryDirectory(dir=DATASET.parent) as directory:
            output = Path(directory) / 'preserved.json'
            output.write_text('preserve', encoding='utf-8')
            with patch('evaluation.broader.evaluate') as evaluate:
                with self.assertRaisesRegex(ValueError, 'already exists'):
                    module.run_split('dev', output)
                evaluate.assert_not_called()
            self.assertEqual(output.read_text(encoding='utf-8'), 'preserve')

    def test_missing_breakdown_changed_intent_or_model_identity_is_rejected(self):
        module = importlib.import_module('evaluation.broader')
        with tempfile.TemporaryDirectory(dir=DATASET.parent) as directory:
            output = Path(directory) / 'synthetic-unit-fixture.json'
            report = self.synthetic_report()
            retain_sources(report, BROADER / 'dev.json', output, BROADER / 'corpus.json')
            module.retain_bundle(report, output)
            for damage in ('breakdown', 'intent', 'model'):
                changed = deepcopy(report)
                if damage == 'breakdown':
                    changed.pop('by_tag')
                elif damage == 'intent':
                    changed['cases'][0]['intent_id'] = 'invented'
                else:
                    changed['model']['revision'] = 'invented'
                output.write_bytes(module.json_bytes(changed))
                with self.subTest(damage=damage), self.assertRaises(ValueError):
                    module.verify_report(output)


if __name__ == '__main__':
    unittest.main()
