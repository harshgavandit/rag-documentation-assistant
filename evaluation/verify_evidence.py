"""Verify retained diagnostic evidence offline; never call a model or rerun timings.

Recompute ranking metrics, timing aggregates, fixture checks and usage totals from
the committed raw records. Check exact-byte source/dataset snapshots, including
the older reports that predate embedded snapshot manifests. This detects drift,
not fabricated provenance: hashes are not signatures or independent attestation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re

from evaluation.adversarial import score_case, summarize as summarize_challenges
from evaluation.evaluate_answers import score_response, summarize as summarize_answers
from evaluation.metrics import ranking_metrics
from evaluation.run import chunk_id, summarize_results

RESULTS = Path(__file__).resolve().parent / 'results'
V1 = 'local-retrieval-2026-10-24.json'
V2 = 'retrieval-v2-2026-10-24.json'
LIVE = 'live-answers-2026-10-24.json'
CHALLENGE = 'adversarial-2026-10-24.json'
METHODS = ('bm25', 'vector', 'hybrid')
REFUSAL = 'I could not find an answer to this question in the provided documentation.'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'),
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite JSON number')))


def compare(recorded, computed, label):
    """Compare complete records; known historical schema differences map at callers."""
    if isinstance(recorded, dict):
        require(isinstance(computed, dict) and set(recorded) == set(computed), label + ': object field mismatch')
        for key, value in recorded.items():
            require(key in computed, label + ': unknown field ' + key)
            compare(value, computed[key], label + '.' + key)
    elif isinstance(recorded, list):
        require(isinstance(computed, list) and len(recorded) == len(computed), label + ': list mismatch')
        for index, value in enumerate(recorded):
            compare(value, computed[index], f'{label}[{index}]')
    elif type(recorded) in (int, float):
        require(type(computed) in (int, float) and math.isfinite(recorded)
                and math.isclose(recorded, computed, rel_tol=1e-12, abs_tol=1e-12), label + ': numeric mismatch')
    else:
        require(type(recorded) is type(computed) and recorded == computed, label + ': value mismatch')


def snapshot(folder, relative, expected):
    path = (folder / relative).resolve()
    require(path.is_relative_to((folder / 'sources').resolve()), 'Snapshot path outside sources')
    require(isinstance(expected, str) and re.fullmatch(r'[a-f0-9]{64}', expected), 'Invalid source hash')
    require(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == expected,
            'Snapshot hash mismatch: ' + relative)
    return path


def verify_snapshot_bindings(folder, reports):
    """Explicit mappings preserve original byte hashes without rewriting reports."""
    old, expanded, live, adversarial = (reports[name] for name in (V1, V2, LIVE, CHALLENGE))
    sources = expanded['source']['exact_byte_snapshots']
    bindings = {'dataset': expanded['dataset']['sha256'], 'corpus': expanded['corpus']['sha256'],
                'engine': expanded['source']['engine_sha256'], 'evaluator': expanded['source']['evaluator_sha256'],
                'metrics': expanded['source']['metrics_sha256'], 'model_manifest': expanded['model']['manifest_sha256']}
    require(set(sources) == set(bindings), 'Incomplete expanded source manifest')
    paths = {}
    for label, expected in bindings.items():
        entry = sources[label]
        require(entry['sha256'] == expected, 'Manifest hash binding mismatch')
        paths[label] = snapshot(folder, entry['path_relative_to_report'], expected)
    legacy = [('cases-2026-10-24.json', old['dataset']['sha256']),
              ('retrieval-run-2026-10-24.py.txt', old['source']['evaluator_sha256']),
              ('metrics-2026-10-24.py.txt', old['source']['metrics_sha256']),
              ('web-engine-2026-10-24.py.txt', old['source']['engine_sha256']),
              ('web-app-2026-10-24.py.txt', live['source_sha256']['web/app.py']),
              ('evaluate_answers-2026-10-24.py.txt', live['source_sha256']['evaluation/evaluate_answers.py']),
              ('adversarial-2026-10-24-dataset.txt', adversarial['dataset_sha256']),
              ('adversarial-2026-10-24-evaluator.txt', adversarial['source_sha256']['evaluation/adversarial.py']),
              ('adversarial-2026-10-24-app.txt', adversarial['source_sha256']['web/app.py']),
              ('adversarial-2026-10-24-grounding.txt', adversarial['source_sha256']['web/grounding.py'])]
    for filename, expected in legacy:
        snapshot(folder, 'sources/' + filename, expected)
    require(old['corpus']['sha256'] == live['corpus_sha256'] == expanded['corpus']['sha256'], 'Corpus identity mismatch')
    require(old['dataset']['sha256'] == live['dataset_sha256'], 'Live dataset identity mismatch')
    require(old['source']['engine_sha256'] == live['source_sha256']['web/engine.py'], 'Live engine identity mismatch')
    compare(old['model'], expanded['model'], 'Original model manifest')
    manifest = read_json(paths['model_manifest'])
    compare(expanded['model']['artifact_sha256'], {k: v['sha256'] for k, v in manifest['files'].items()}, 'Model artifact binding')
    return read_json(folder / 'sources/cases-2026-10-24.json'), read_json(paths['dataset']), read_json(paths['corpus']), read_json(folder / 'sources/adversarial-2026-10-24-dataset.txt')


def validate_dataset(dataset, corpus):
    chunks = {chunk_id(c): c for c in corpus}
    require(len(chunks) == len(corpus) == dataset['corpus']['chunk_count'], 'Corpus count mismatch')
    ids = [c['id'] for c in dataset['cases']]
    require(len(ids) == len(set(ids)), 'Duplicate dataset IDs')
    for case in dataset['cases']:
        gold = case['relevant_chunk_ids']
        require(type(case['answerable']) is bool and case['answerable'] == bool(gold), 'Invalid answerable label')
        require(len(gold) == len(set(gold)) and set(gold).issubset(chunks), 'Unknown or duplicate gold label')
        require({e['chunk_id'] for e in case['evidence']} == set(gold), 'Missing source evidence')
        for evidence in case['evidence']:
            require(bool(evidence['excerpt'].strip()) and evidence['excerpt'] in chunks[evidence['chunk_id']]['content'], 'Evidence excerpt mismatch')
    return chunks


def verify_retrieval(report, dataset, corpus):
    known = validate_dataset(dataset, corpus)
    rows = report['cases']
    require([r['case_id'] for r in rows] == [c['id'] for c in dataset['cases']], 'Retrieval case coverage mismatch')
    require(report['dataset']['id'] == dataset['dataset_id'], 'Dataset identity mismatch')
    require(report['dataset']['case_count'] == len(rows), 'Dataset case denominator mismatch')
    require(report['dataset']['answerable_case_count'] == sum(c['answerable'] for c in dataset['cases']), 'Answerable denominator mismatch')
    for row, case in zip(rows, dataset['cases']):
        for field in ('question', 'tags', 'answerable', 'relevant_chunk_ids'):
            compare(row[field], case[field], 'Case fixture ' + field)
        require(set(row['methods']) == set(METHODS), 'Missing retrieval method')
        for method in METHODS:
            measured = row['methods'][method]
            ranked = measured['ranked_chunk_ids']
            require(len(ranked) <= 5 and len(ranked) == len(set(ranked)) and set(ranked).issubset(known), 'Invalid ranked chunk IDs')
            require(len(measured['warm_latency_samples_ms']) == report['protocol']['warm_repeats_per_case'], 'Timing sample denominator mismatch')
            expected = ranking_metrics(ranked, case['relevant_chunk_ids']) if case['answerable'] else None
            require(measured['metrics'] is None if expected is None else set(measured['metrics']) == set(expected), 'Missing metric fields')
            compare(measured['metrics'], expected, 'Raw ranking metrics')
            if 'missed_relevant_chunk_ids' in measured:
                compare(measured['missed_relevant_chunk_ids'], [c for c in case['relevant_chunk_ids'] if c not in ranked], 'Missing chunk calculation')
    for method in METHODS:
        summary = summarize_results(rows, method)
        if dataset['dataset_id'] == 'docwise-authored-retrieval-v1':
            # This recorded schema predates these two fields; do not rewrite it.
            for field in ('case_count', 'miss_at_5_case_ids'):
                summary.pop(field)
        compare(report['summary'][method], summary, 'Retrieval summary ' + method)
    if 'by_tag' in report:
        require(set(report['by_tag']) == {t for r in rows for t in r['tags']}, 'Tag coverage mismatch')
        for tag, methods in report['by_tag'].items():
            require(set(methods) == set(METHODS), 'Missing per-tag method')
            for method in METHODS:
                compare(methods[method], summarize_results([r for r in rows if tag in r['tags']], method), 'Tag summary ' + tag)


def verify_generation(report, cases, corpus, adversarial=False):
    rows = report['cases']
    require([r['id'] for r in rows] == [c['id'] for c in cases], 'Generation case coverage mismatch')
    require(type(report['provider_requests']) is int and len(rows) == report['provider_requests'] <= report['request_budget'] <= 30, 'Provider request denominator mismatch')
    for row, case in zip(rows, cases):
        compare(row['question'], case['question'], 'Generation question')
        require(row['status'] in ('completed', 'error'), 'Invalid generation status')
        for value in (row.get('usage') or {}).values():
            require(value is None or type(value) is int and value >= 0, 'Invalid token usage')
        usage = row.get('usage') or {}
        if all(usage.get(k) is not None for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')):
            require(usage['prompt_tokens'] + usage['completion_tokens'] == usage['total_tokens'], 'Token total mismatch')
        if row['status'] != 'completed':
            continue
        require(row['finish_reason'] == 'stop', 'Incomplete retained output')
        require(row['result']['answer'] == row['provider_output'], 'Provider output and accepted answer differ')
        result = row['result']
        require(type(result['refused']) is bool and result['refused'] == (result['answer'] == REFUSAL), 'Refusal state mismatch')
        labels = list(dict.fromkeys(re.findall(r'\[Source: .+?, Section: .+?\]', result['answer'])))
        compare(result['citations'], labels, 'Citations extracted from answer')
        if adversarial:
            compare(row['chunks'], case['chunks'], 'Challenge context')
            checks = score_case(case, row['result'])
        else:
            chunks = []
            for source in row['retrieved_sources']:
                matches = [c for c in corpus if c['metadata']['doc_title'] == source['doc']
                           and c['metadata']['section_title'] == source['section']
                           and c['metadata']['chunk_index'] == source['chunk_index']]
                require(len(matches) == 1, 'Unknown retained source')
                chunks.extend(matches)
            checks = score_response(case, row['result'], chunks)
        compare(row['checks'], checks, 'Generation fixture checks')
    summary = summarize_challenges(rows) if adversarial else summarize_answers(rows)
    if not adversarial:
        # The v1 answer runner's policy predates explicit complete-usage/subtotal fields.
        for field in ('records_with_complete_usage', 'known_token_subtotals'):
            summary.pop(field)
    compare(report['summary'], summary, 'Generation summary')


def verify_directory(folder=RESULTS):
    reports = {name: read_json(folder / name) for name in (V1, V2, LIVE, CHALLENGE)}
    old, expanded, corpus, challenges = verify_snapshot_bindings(folder, reports)
    verify_retrieval(reports[V1], old, corpus)
    verify_retrieval(reports[V2], expanded, corpus)
    verify_generation(reports[LIVE], [c for c in old['cases'] if c.get('live_sample')], corpus)
    require(reports[CHALLENGE]['dataset_id'] == challenges['dataset_id'], 'Challenge dataset identity mismatch')
    verify_generation(reports[CHALLENGE], challenges['cases'], corpus, adversarial=True)
    return {'verified': True, 'reports_verified': len(reports), 'retrieval_cases': len(old['cases']) + len(expanded['cases']),
            'retained_model_cases': len(reports[LIVE]['cases']) + len(reports[CHALLENGE]['cases']),
            'provider_requests_made': 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, default=RESULTS)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(verify_directory(args.results), sort_keys=True))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as error:
        print('Evidence verification failed: ' + str(error))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
