"""Explicitly authorized, at-most-24 real answers on frozen heldout query domains.

Uses the production answer guard and encoder on a separate corpus. This is local
retrieval plus real provider generation, not hosted latency or human grading.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

from evaluation.request_budget import RequestBudget
from evaluation.evaluate_answers import usage_fields
from evaluation.run import ROOT, load_dataset, sha256

SOURCE_FILES = ('web/app.py', 'web/engine.py', 'web/grounding.py', 'web/staging.py',
                'evaluation/broader_answers.py', 'evaluation/request_budget.py')
PRICING = {'source': 'https://developers.openai.com/api/docs/models/gpt-6-luna',
           'checked_date': '2026-10-25', 'service_tier': 'standard',
           'input_usd_per_million': 0.1, 'cached_input_usd_per_million': 0.01,
           'output_usd_per_million': 0.5}


def checks(case, result, chunks):
    allowed = {f"[Source: {c['metadata']['doc_title']}, Section: {c['metadata']['section_title']}]" for c in chunks}
    labels = result.get('citations', [])
    return {'expected_answerable': case['answerable'], 'refused': result['refused'],
            'expected_refusal_matches': result['refused'] == (not case['answerable']),
            'non_refusal_labels_valid': bool(labels) and set(labels).issubset(allowed) if not result['refused'] else None,
            'retrieved_gold_chunk_count': len(set(case['relevant_chunk_ids']).intersection(
                f"{c['metadata']['doc_title']}::{c['metadata']['chunk_index']:03}" for c in chunks)),
            'semantic_correctness_human_reviewed': False}


def summarize(rows):
    done = [r for r in rows if r['status'] == 'completed']
    keys = ('prompt_tokens', 'completion_tokens', 'total_tokens')
    usage = [r.get('usage') or {} for r in rows]
    known = {key: sum(u[key] for u in usage if u.get(key) is not None) for key in keys}
    estimate = 0.0
    priced = 0
    for u in usage:
        if all(type(u.get(k)) is int for k in keys):
            cached = u.get('cached_prompt_tokens')
            if type(cached) is int and 0 <= cached <= u['prompt_tokens']:
                estimate += ((u['prompt_tokens'] - cached) * PRICING['input_usd_per_million']
                    + cached * PRICING['cached_input_usd_per_million']
                    + u['completion_tokens'] * PRICING['output_usd_per_million']) / 1_000_000
                priced += 1
    return {'attempted_cases': len(rows), 'completed_cases': len(done), 'error_cases': len(rows) - len(done),
        'decision_proxies_passed': sum(r['checks']['expected_refusal_matches'] for r in done),
        'non_refusal_label_proxies_passed': sum(r['checks']['non_refusal_labels_valid'] is True for r in done),
        'unsupported_refusals': sum(r['checks']['refused'] and not r['checks']['expected_answerable'] for r in done),
        'known_token_subtotals': known,
        'records_with_complete_usage': sum(all(type(u.get(k)) is int for k in keys) for u in usage),
        'records_priced_from_returned_usage': priced,
        'returned_usage_estimate_usd': round(estimate, 10) if priced else None,
        'cost_scope': 'Published Standard-rate estimate for returned complete usage and cached-input counters only; not a provider invoice or complete billed-cost reconciliation.',
        'human_evaluated_answers': 0}


def save(path, report):
    report['summary'] = summarize(report['cases'])
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def verify(path):
    report = json.loads(path.read_text(encoding='utf-8'))
    dataset, corpus = load_dataset(ROOT / 'evaluation/broader_v1/heldout.json', ROOT / 'evaluation/broader_v1/corpus.json')
    if (report['dataset_sha256'] != sha256(ROOT / 'evaluation/broader_v1/heldout.json') or
            report['corpus_sha256'] != sha256(ROOT / 'evaluation/broader_v1/corpus.json') or
            report['max_completion_tokens'] != 800 or report['request_limit'] != 24 or
            len(report['cases']) > 24 or report['provider_requests'] != len(report['cases'])):
        raise ValueError('Answer report identity or budget mismatch')
    if [r['id'] for r in report['cases']] != [c['id'] for c in dataset['cases'][:len(report['cases'])]]:
        raise ValueError('Answer case coverage mismatch')
    by_id = {f"{c['metadata']['doc_title']}::{c['metadata']['chunk_index']:03}": c for c in corpus}
    for row, case in zip(report['cases'], dataset['cases']):
        if row['question'] != case['question']:
            raise ValueError('Answer question mismatch')
        chunks = [by_id[cid] for cid in row['retrieved_chunk_ids']]
        if row['status'] == 'completed' and row['checks'] != checks(case, row['result'], chunks):
            raise ValueError('Answer proxy mismatch')
        usage = row.get('usage') or {}
        if all(type(usage.get(k)) is int for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')):
            if usage['prompt_tokens'] + usage['completion_tokens'] != usage['total_tokens']:
                raise ValueError('Answer token arithmetic mismatch')
        if type(usage.get('completion_tokens')) is int and usage['completion_tokens'] > 800:
            raise ValueError('Returned output exceeds authorized cap')
    for name, entry in report['source_snapshots'].items():
        snapshot = (path.parent / entry['path']).resolve()
        if not snapshot.is_relative_to(path.parent.resolve()) or sha256(snapshot) != entry['sha256']:
            raise ValueError('Answer source snapshot mismatch')
    if report['pricing'] != PRICING or report['summary'] != summarize(report['cases']):
        raise ValueError('Answer aggregate mismatch')
    return {'verified': True, 'provider_requests': report['provider_requests'], 'new_provider_requests': 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--budget-ledger', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--verify-report', type=Path)
    args = parser.parse_args(argv)
    if args.verify_report:
        print(json.dumps(verify(args.verify_report), sort_keys=True))
        return 0
    if not args.live or not args.output or not args.budget_ledger or not args.env_file:
        parser.error('Live runs require explicit --live, private --env-file, --budget-ledger and new --output')
    if args.output.exists() or not args.output.parent.is_dir():
        parser.error('Output must be a new file in an existing directory')
    from evaluation.broader import validate_bundle
    validate_bundle(require_review=True)  # Frozen reviewed source labels, before paid work.
    dataset, corpus = load_dataset(ROOT / 'evaluation/broader_v1/heldout.json', ROOT / 'evaluation/broader_v1/corpus.json')
    if len(dataset['cases']) != 24:
        parser.error('Expected frozen 24-case heldout sample')
    from dotenv import dotenv_values
    values = dotenv_values(args.env_file)
    key = values.get('OPENAI_API_KEY') or values.get('OPEN_AI_API_KEY')
    if not key:
        parser.error('Private provider credential unavailable; no requests sent')
    os.environ.update(OPENAI_API_KEY=key, OPENAI_MODEL='gpt-6-luna', OPENAI_MAX_COMPLETION_TOKENS='800')
    if os.getenv('STAGING_WORKLOAD_ENABLED') == '1':
        parser.error('Refuse live evaluation with staging fixtures enabled')
    budget = RequestBudget(args.budget_ledger, authorization='user-approved-2026-10-25-24-additional', limit=24, output_cap=800)
    if budget.snapshot()['attempts']:
        parser.error('This allowance already has attempts; no automatic rerun or refund')
    sys.path.insert(0, str(ROOT / 'web'))
    site = importlib.import_module('app')
    from web.engine import Index
    index = Index(chunks=corpus)
    report = {'schema_version': 1, 'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
        'scope': 'Real provider generation with production answer guards and local ONNX retrieval over a separate frozen multi-domain diagnostic corpus. No hosted admission or real-user claim.',
        'grading': 'Expected refusal decisions and citation-label membership proxies only. Semantic correctness and independent human evaluation remain unmeasured.',
        'requested_model': 'gpt-6-luna', 'max_completion_tokens': 800, 'request_limit': 24,
        'dataset_sha256': sha256(ROOT / 'evaluation/broader_v1/heldout.json'),
        'corpus_sha256': sha256(ROOT / 'evaluation/broader_v1/corpus.json'),
        'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'working_tree_dirty': bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip()),
        'pricing': PRICING, 'provider_requests': 0, 'source_snapshots': {}, 'cases': []}
    for name in SOURCE_FILES:
        original = ROOT / name
        snapshot = args.output.parent / 'sources' / (args.output.stem + '-' + name.replace('/', '_') + '.txt')
        with snapshot.open('xb') as handle:
            handle.write(original.read_bytes())
        report['source_snapshots'][name] = {'path': snapshot.relative_to(args.output.parent).as_posix(), 'sha256': sha256(snapshot)}
    with args.output.open('x', encoding='utf-8') as handle:
        handle.write('{}\n')
    save(args.output, report)
    original_post = site.post_json
    for case in dataset['cases']:
        started = time.perf_counter()
        chunks = index.search(case['question'])
        row = {'id': case['id'], 'question': case['question'],
               'retrieved_chunk_ids': [f"{c['metadata']['doc_title']}::{c['metadata']['chunk_index']:03}" for c in chunks],
               'retrieval_ms': round((time.perf_counter() - started) * 1000, 3)}
        captured = {}
        def measured_post(url, payload, token, timeout=15):
            if url != 'https://api.openai.com/v1/chat/completions' or payload.get('max_completion_tokens') != 800:
                raise ValueError('Unexpected destination or output cap')
            budget.reserve(case['id'])
            report['provider_requests'] += 1
            save(args.output, report)  # Persist attempt before network, including failure.
            before = time.perf_counter()
            try:
                response = original_post(url, payload, token, timeout)
                choice = (response.get('choices') or [{}])[0]
                captured.update(usage=usage_fields(response), returned_model=response.get('model'),
                    finish_reason=choice.get('finish_reason'), provider_output=(choice.get('message') or {}).get('content'))
                return response
            finally:
                captured['provider_ms'] = round((time.perf_counter() - before) * 1000, 3)
        try:
            with patch.object(site, 'post_json', measured_post):
                result = site.answer(case['question'], chunks)
            row.update(status='completed', result=result, checks=checks(case, result, chunks))
        except Exception as error:
            row.update(status='error', error_type=type(error).__name__, http_status=getattr(error, 'status_code', None))
        row.update(captured)
        row['elapsed_ms'] = round((time.perf_counter() - started) * 1000, 3)
        report['cases'].append(row)
        save(args.output, report)
        print(case['id'], row['status'], report['provider_requests'], '/ 24', flush=True)
        if row['status'] == 'error' and row.get('http_status') != 502:
            return 1
    print(json.dumps(report['summary'], sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
