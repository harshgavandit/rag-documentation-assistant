"""Run source-grounded retrieval evaluation without credentials or network calls.

Usage: python -m evaluation.run --output evaluation/results/local.json
Validate corpus and gold evidence only: python -m evaluation.run --validate-only
"""
import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
from time import perf_counter

from evaluation.metrics import ranking_metrics, timing_summary

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / 'evaluation/cases.json'
CORPUS = ROOT / 'web/data/corpus.json'
METHODS = ('bm25', 'vector', 'hybrid')


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def chunk_id(chunk):
    metadata = chunk['metadata']
    return f"{metadata['doc_title']}::{metadata['chunk_index']:03d}"


def load_dataset(dataset_path=DATASET, corpus_path=CORPUS):
    """Reject stale labels, duplicate IDs, unknown chunks and unsupported evidence."""
    dataset = json.loads(dataset_path.read_text(encoding='utf-8-sig'))
    corpus = json.loads(corpus_path.read_text(encoding='utf-8-sig'))
    if dataset.get('schema_version') != 1:
        raise ValueError('Unsupported dataset schema')
    if dataset['corpus']['sha256'] != sha256(corpus_path):
        raise ValueError('Stale corpus: review and version the gold labels before running')
    if dataset['corpus']['chunk_count'] != len(corpus):
        raise ValueError('Corpus chunk count mismatch')
    chunks = {chunk_id(chunk): chunk for chunk in corpus}
    if len(chunks) != len(corpus):
        raise ValueError('Duplicate corpus chunk IDs')
    case_ids = set()
    for case in dataset['cases']:
        if not re.fullmatch(r'docwise-\d{3}', case['id']) or case['id'] in case_ids:
            raise ValueError('Invalid or duplicate case ID')
        case_ids.add(case['id'])
        if not case['question'].strip() or not case['tags']:
            raise ValueError('Missing question or tags')
        relevant = case['relevant_chunk_ids']
        if len(relevant) != len(set(relevant)) or any(cid not in chunks for cid in relevant):
            raise ValueError('Unknown or duplicate relevant chunk ID')
        if type(case['answerable']) is not bool or case['answerable'] != bool(relevant):
            raise ValueError('Answerable flag and relevance labels disagree')
        evidence_ids = set()
        for evidence in case['evidence']:
            cid, excerpt = evidence['chunk_id'], evidence['excerpt']
            if cid not in relevant or not excerpt.strip() or excerpt not in chunks[cid]['content']:
                raise ValueError('Gold excerpt does not match its relevant source chunk')
            evidence_ids.add(cid)
        if evidence_ids != set(relevant):
            raise ValueError('Each relevant chunk needs an explicit supporting excerpt')
        if not case['answerable'] and not case.get('unsupported_reason', '').strip():
            raise ValueError('Unsupported case needs a source-scope explanation')
    if not case_ids or not any(c['answerable'] for c in dataset['cases']):
        raise ValueError('Dataset needs answerable cases')
    return dataset, corpus


def validate_artifacts():
    manifest_path = ROOT / 'web/model_manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    artifacts = {}
    for name, info in manifest['files'].items():
        path = ROOT / 'web/models' / name
        if not path.is_file() or sha256(path) != info['sha256']:
            raise ValueError('Missing or mismatched model artifact; run python web/prepare.py')
        artifacts[name] = info['sha256']
    return {'model': manifest['model'], 'revision': manifest['revision'],
            'artifact_sha256': artifacts, 'manifest_sha256': sha256(manifest_path)}


def make_retrievers(index, np):
    """Use exact production encoding and positive BM25 behavior for baselines.

    Hybrid calls Index.search unchanged (RRF k=60, 10 candidates per leg at top_k=5).
    BM25 ties use corpus order, matching production lexical sorting; vector ties
    use numpy.argsort, matching production semantic sorting.
    """
    def bm25(question):
        scores = index.bm25.get_scores(re.findall(r'\w+', question.lower()))
        indices = sorted((i for i, score in enumerate(scores) if score > 0),
                         key=lambda i: scores[i], reverse=True)[:5]
        return [chunk_id(index.chunks[i]) for i in indices]
    def vector(question):
        scores = index.vectors @ index.encode([question])[0]
        return [chunk_id(index.chunks[i]) for i in np.argsort(-scores)[:5].tolist()]
    return {'bm25': bm25, 'vector': vector,
            'hybrid': lambda question: [chunk_id(c) for c in index.search(question, top_k=5)]}


def git_metadata():
    def git(*args):
        result = subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True, text=True, check=True)
        return result.stdout.strip()
    return {'head_sha': git('rev-parse', 'HEAD'),
            'working_tree_dirty': bool(git('status', '--porcelain')),
            'engine_sha256': sha256(ROOT / 'web/engine.py'),
            'evaluator_sha256': sha256(Path(__file__)),
            'metrics_sha256': sha256(ROOT / 'evaluation/metrics.py')}


def evaluate(dataset, corpus, repeats=3, warmup_rounds=1):
    model = validate_artifacts()
    # Import and construction measured separately. OS/filesystem caches are not cleared.
    started = perf_counter()
    import numpy as np
    from web.engine import Index
    import_ms = (perf_counter() - started) * 1000
    started = perf_counter()
    index = Index()
    construction_ms = (perf_counter() - started) * 1000
    if index.chunks != corpus:
        raise ValueError('Production index corpus differs from the frozen evaluation corpus')
    retrieve = make_retrievers(index, np)
    first_query = {}
    for method in METHODS:
        started = perf_counter()
        retrieve[method](dataset['cases'][0]['question'])
        first_query[method] = (perf_counter() - started) * 1000
    for _ in range(warmup_rounds):
        for case in dataset['cases']:
            for method in METHODS:
                retrieve[method](case['question'])
    known_ids = {chunk_id(chunk) for chunk in corpus}
    results = []
    warm_samples = {method: [] for method in METHODS}
    for case in dataset['cases']:
        methods = {}
        for method in METHODS:
            samples, rankings = [], []
            for _ in range(repeats):
                started = perf_counter()
                ranking = retrieve[method](case['question'])
                samples.append((perf_counter() - started) * 1000)
                if len(ranking) != len(set(ranking)) or any(cid not in known_ids for cid in ranking):
                    raise ValueError('Retriever returned invalid chunk IDs')
                rankings.append(ranking)
            if any(ranking != rankings[0] for ranking in rankings):
                raise ValueError('Retrieval rankings varied between repetitions')
            warm_samples[method].extend(samples)
            methods[method] = {'ranked_chunk_ids': rankings[0], 'warm_latency_samples_ms': samples,
                               'metrics': ranking_metrics(rankings[0], case['relevant_chunk_ids']) if case['answerable'] else None}
        results.append({'case_id': case['id'], 'question': case['question'], 'tags': case['tags'],
                        'answerable': case['answerable'], 'relevant_chunk_ids': case['relevant_chunk_ids'], 'methods': methods})
    summaries = {}
    for method in METHODS:
        answerable = [row['methods'][method]['metrics'] for row in results if row['answerable']]
        summaries[method] = {'answerable_case_count': len(answerable),
            'metrics': {key: sum(row[key] for row in answerable) / len(answerable) for key in answerable[0]},
            'warm_retrieval_latency_all_cases': timing_summary(warm_samples[method]),
            'unsupported_cases': {'count': sum(not row['answerable'] for row in results),
                'returned_nonempty_count': sum(bool(row['methods'][method]['ranked_chunk_ids']) for row in results if not row['answerable']),
                'interpretation': 'Diagnostic rankings only. No retrieval abstention threshold or answer/refusal accuracy is measured.'}}
    return {'schema_version': 1, 'evaluation_kind': 'local_authored_retrieval_diagnostic',
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'dataset': {'id': dataset['dataset_id'], 'label': dataset['label'], 'sha256': sha256(DATASET),
                    'case_count': len(dataset['cases']), 'answerable_case_count': sum(c['answerable'] for c in dataset['cases'])},
        'corpus': dataset['corpus'], 'source': git_metadata(), 'model': model,
        'runtime': {'python': platform.python_version(), 'os': platform.system(), 'os_release': platform.release(),
            'machine': platform.machine(), 'logical_cpu_count': os.cpu_count(),
            'packages': {package: version(package) for package in ('numpy', 'onnxruntime', 'tokenizers', 'rank-bm25')},
            'onnx_provider': 'CPUExecutionProvider', 'onnx_intra_op_threads': 2, 'onnx_inter_op_threads': 1},
        'protocol': {'top_k': 5, 'warm_repeats_per_case': repeats, 'excluded_warmup_rounds': warmup_rounds,
            'method_order': list(METHODS), 'relevance': 'binary; unlisted chunks treated as non-relevant',
            'aggregation': 'macro mean over answerable queries; unsupported queries excluded from ranking metrics',
            'percentiles': 'linear interpolation at (sample_count - 1) * p',
            'latency_scope': 'local retrieval calls only; excludes HTTP, network, generation, rate limiting and model downloads',
            'cold_scope': 'one fresh evaluator process; import and index construction separated; OS/filesystem caches not cleared; first method calls share one index',
            'limitations': 'Small authored diagnostic set derived from two educational documents; not held out, external, traffic weighted, production telemetry or an answer-generation benchmark. No retrieval tuning performed.'},
        'startup': {'dependency_import_ms': import_ms, 'index_construction_ms': construction_ms,
                    'first_query_per_method_ms': first_query},
        'summary': summaries, 'cases': results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'evaluation/results/local.json')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--warmup-rounds', type=int, default=1)
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args(argv)
    if args.repeats < 1 or args.warmup_rounds < 1:
        parser.error('repeats and warmup-rounds must be at least 1')
    try:
        dataset, corpus = load_dataset()
        if args.validate_only:
            print(json.dumps({'valid': True, 'cases': len(dataset['cases']), 'chunks': len(corpus), 'corpus_sha256': sha256(CORPUS)}, sort_keys=True))
            return 0
        report = evaluate(dataset, corpus, args.repeats, args.warmup_rounds)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + '\n', encoding='utf-8')
        print(json.dumps({'completed': True, 'summary': report['summary']}, sort_keys=True, allow_nan=False))
        return 0
    except ValueError as error:
        # Only validation errors created here; do not print environment or provider data.
        print('Evaluation validation failed: ' + str(error), file=sys.stderr)
    except Exception:
        print('Evaluation failed. Check local dependencies, Git checkout and prepared model artifacts; no report was written.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
