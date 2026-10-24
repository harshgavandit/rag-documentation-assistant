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
import re
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
AUTHORIZATION = 'user-approved-2026-10-25-24-additional'


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


def artifact(path, entry, root):
    if (not isinstance(entry, dict) or set(entry) != {'path', 'sha256'} or
            not isinstance(entry.get('path'), str) or
            not re.fullmatch(r'[a-f0-9]{64}', str(entry.get('sha256', '')))):
        raise ValueError('Invalid answer source snapshot entry')
    relative = Path(entry['path'])
    snapshot = (root / relative).resolve()
    if (relative.is_absolute() or len(relative.parts) != 2 or relative.parts[0] != 'sources' or
            '..' in relative.parts or not snapshot.is_relative_to(root.resolve()) or
            not snapshot.is_file() or sha256(snapshot) != entry['sha256']):
        raise ValueError('Answer source snapshot mismatch')
    return snapshot


def verify(path, *, _artifact_root=None, _depth=0):
    if _depth > 24:
        raise ValueError('Continuation archive depth exceeds the request bound')
    artifact_root = path.parent if _artifact_root is None else _artifact_root
    report = json.loads(path.read_text(encoding='utf-8'))
    dataset, corpus = load_dataset(ROOT / 'evaluation/broader_v1/heldout.json', ROOT / 'evaluation/broader_v1/corpus.json')
    if (not isinstance(report, dict) or report.get('schema_version') != 1 or
            report.get('requested_model') != 'gpt-6-luna' or
            not re.fullmatch(r'[a-f0-9]{40}', str(report.get('source_commit', ''))) or
            type(report.get('working_tree_dirty')) is not bool or
            report.get('dataset_sha256') != sha256(ROOT / 'evaluation/broader_v1/heldout.json') or
            report['corpus_sha256'] != sha256(ROOT / 'evaluation/broader_v1/corpus.json') or
            type(report.get('max_completion_tokens')) is not int or report['max_completion_tokens'] != 800 or
            type(report.get('request_limit')) is not int or report['request_limit'] != 24 or
            not isinstance(report.get('cases'), list) or len(report['cases']) > 24 or
            type(report.get('provider_requests')) is not int or report['provider_requests'] != len(report['cases'])):
        raise ValueError('Answer report identity or budget mismatch')
    if [r['id'] for r in report['cases']] != [c['id'] for c in dataset['cases'][:len(report['cases'])]]:
        raise ValueError('Answer case coverage mismatch')
    by_id = {f"{c['metadata']['doc_title']}::{c['metadata']['chunk_index']:03}": c for c in corpus}
    for row, case in zip(report['cases'], dataset['cases']):
        if row['question'] != case['question']:
            raise ValueError('Answer question mismatch')
        selected = row.get('retrieved_chunk_ids')
        if (not isinstance(selected, list) or len(selected) != 5 or
                any(not isinstance(cid, str) or cid not in by_id for cid in selected) or len(set(selected)) != 5):
            raise ValueError('Answer retrieved source inventory mismatch')
        chunks = [by_id[cid] for cid in selected]
        if row.get('status') not in {'completed', 'error'}:
            raise ValueError('Invalid answer status')
        if row['status'] == 'completed':
            raw = row.get('provider_output')
            if not isinstance(raw, str) or not raw.strip() or row.get('finish_reason') != 'stop':
                raise ValueError('Completed answer lacks complete retained provider output')
            text = raw.strip()
            refusal = 'I could not find an answer to this question in the provided documentation.'
            labels = re.findall(r'\[Source: .+?, Section: .+?\]', text)
            allowed = {f"[Source: {c['metadata']['doc_title']}, Section: {c['metadata']['section_title']}]" for c in chunks}
            if text.strip('\'"') == refusal:
                text, labels = refusal, []
            elif not labels or any(label not in allowed for label in labels):
                raise ValueError('Retained provider output fails production citation guard')
            expected_result = {'answer': text, 'citations': list(dict.fromkeys(labels)), 'refused': text == refusal}
            if (not isinstance(row.get('result'), dict) or type(row['result'].get('refused')) is not bool or
                    row['result'] != expected_result or row.get('checks') != checks(case, expected_result, chunks)):
                raise ValueError('Answer result or proxy mismatch against retained provider output')
        elif 'result' in row or 'checks' in row:
            raise ValueError('Failed answer must not claim a completed result')
        raw_usage = row.get('usage')
        if raw_usage is not None and not isinstance(raw_usage, dict):
            raise ValueError('Invalid returned token usage object')
        usage = raw_usage or {}
        usage_keys = {'prompt_tokens', 'completion_tokens', 'total_tokens', 'cached_prompt_tokens', 'reasoning_tokens'}
        if not isinstance(usage, dict) or (usage and set(usage) != usage_keys):
            raise ValueError('Invalid returned token usage inventory')
        if any(value is not None and (type(value) is not int or not 0 <= value <= 10000000) for value in usage.values()):
            raise ValueError('Returned token usage must be bounded nonnegative integers or unknown')
        if all(type(usage.get(k)) is int for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')):
            if usage['prompt_tokens'] + usage['completion_tokens'] != usage['total_tokens']:
                raise ValueError('Answer token arithmetic mismatch')
        if type(usage.get('completion_tokens')) is int and usage['completion_tokens'] > 800:
            raise ValueError('Returned output exceeds authorized cap')
        for detail, total in (('cached_prompt_tokens', 'prompt_tokens'), ('reasoning_tokens', 'completion_tokens')):
            if type(usage.get(detail)) is int and (type(usage.get(total)) is not int or usage[detail] > usage[total]):
                raise ValueError('Returned token detail exceeds its total')
    if not isinstance(report.get('source_snapshots'), dict) or set(report['source_snapshots']) != set(SOURCE_FILES):
        raise ValueError('Incomplete answer source snapshot inventory')
    for name, entry in report['source_snapshots'].items():
        artifact(path, entry, artifact_root)
    segments = report.get('continuation_segments', [])
    if not isinstance(segments, list) or len(segments) > 24:
        raise ValueError('Invalid continuation inventory')
    # The latest immutable before-report contains the earlier segment inventory.
    # Follow one prior report at each level to avoid exponential traversal.
    for ordinal, segment in enumerate(segments, 1):
        expected_keys = {'segment_index', 'recorded_at_utc', 'source_commit', 'working_tree_dirty',
                         'before_case_count', 'before_report', 'runner_source', 'budget_authorization',
                         'request_limit', 'max_completion_tokens', 'ledger_path_sha256'}
        if (not isinstance(segment, dict) or set(segment) != expected_keys or
                type(segment.get('segment_index')) is not int or segment['segment_index'] != ordinal or
                type(segment.get('before_case_count')) is not int or not 1 <= segment['before_case_count'] < 24 or
                segment['before_case_count'] > len(report['cases']) or
                type(segment.get('working_tree_dirty')) is not bool or
                not re.fullmatch(r'[a-f0-9]{40}', str(segment.get('source_commit', ''))) or
                not re.fullmatch(r'[a-f0-9]{64}', str(segment.get('ledger_path_sha256', ''))) or
                segment.get('budget_authorization') != AUTHORIZATION or
                type(segment.get('request_limit')) is not int or segment['request_limit'] != 24 or
                type(segment.get('max_completion_tokens')) is not int or segment['max_completion_tokens'] != 800):
            raise ValueError('Invalid continuation identity or budget')
        try:
            stamp = datetime.fromisoformat(segment['recorded_at_utc'])
            if stamp.utcoffset() is None:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise ValueError('Invalid continuation time') from None
        artifact(path, segment['before_report'], artifact_root)
        artifact(path, segment['runner_source'], artifact_root)
        if ordinal > 1 and segment['ledger_path_sha256'] != segments[0]['ledger_path_sha256']:
            raise ValueError('Continuation changed the private ledger binding')
    if segments:
        segment = segments[-1]
        before_path = artifact(path, segment['before_report'], artifact_root)
        verify(before_path, _artifact_root=artifact_root, _depth=_depth+1)
        before = json.loads(before_path.read_text(encoding='utf-8'))
        count = segment['before_case_count']
        if (len(before['cases']) != count or before['provider_requests'] != count or
                report['cases'][:count] != before['cases'] or
                before.get('continuation_segments', []) != segments[:-1]):
            raise ValueError('Continuation changed prior rows or segment inventory')
        for name in ('schema_version', 'recorded_at_utc', 'source_commit', 'working_tree_dirty',
                     'scope', 'grading', 'requested_model', 'max_completion_tokens', 'request_limit',
                     'dataset_sha256', 'corpus_sha256', 'pricing', 'source_snapshots'):
            if before.get(name) != report.get(name):
                raise ValueError('Continuation changed original report identity or snapshots')
    if report['pricing'] != PRICING or report['summary'] != summarize(report['cases']):
        raise ValueError('Answer aggregate mismatch')
    return {'verified': True, 'provider_requests': report['provider_requests'], 'new_provider_requests': 0,
            'complete_sample': len(report['cases']) == 24}


def prepare_resume(path, ledger_path):
    """Validate an existing incomplete report and the original ledger before any API work."""
    if not path.is_file() or not ledger_path.is_file():
        raise ValueError('Resume requires an existing report and existing private ledger')
    verify(path)
    report = json.loads(path.read_text(encoding='utf-8'))
    count = len(report['cases'])
    if not 1 <= count < 24:
        raise ValueError('Resume requires a nonempty incomplete sample; completed runs cannot replay')
    budget = RequestBudget(ledger_path, authorization=AUTHORIZATION, limit=24, output_cap=800)
    if budget.lock.exists():
        raise ValueError('Resume cannot proceed while a reservation lock exists')
    attempts = budget.snapshot()['attempts']
    if len(attempts) != count or [a['case_id'] for a in attempts] != [r['id'] for r in report['cases']]:
        raise ValueError('Existing ledger attempts must exactly match the retained report prefix')
    binding = hashlib.sha256(str(ledger_path.resolve()).encode()).hexdigest()
    if report.get('continuation_segments') and report['continuation_segments'][0]['ledger_path_sha256'] != binding:
        raise ValueError('Resume must use the same private ledger path')
    for name in SOURCE_FILES:
        if name != 'evaluation/broader_answers.py' and sha256(ROOT/name) != report['source_snapshots'][name]['sha256']:
            raise ValueError('Continuation cannot change original production or budget dependencies')
    return report, budget, count


def attach_continuation(path, report, budget):
    """Archive exact original bytes and new runner source before appending any attempt."""
    ordinal = len(report.get('continuation_segments', []))+1
    folder = path.parent/'sources'
    before_path = folder/f'{path.stem}-continuation-{ordinal:02d}-before-report.txt'
    runner_path = folder/f'{path.stem}-continuation-{ordinal:02d}-runner.txt'
    before_bytes = path.read_bytes()
    if json.loads(before_bytes) != report:
        raise ValueError('Report changed after resume validation')
    with before_path.open('xb') as handle:
        handle.write(before_bytes)
    with runner_path.open('xb') as handle:
        handle.write(Path(__file__).read_bytes())
    report.setdefault('continuation_segments', []).append({
        'segment_index': ordinal, 'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
        'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'working_tree_dirty': bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip()),
        'before_case_count': len(report['cases']), 'budget_authorization': AUTHORIZATION,
        'request_limit': 24, 'max_completion_tokens': 800,
        'ledger_path_sha256': hashlib.sha256(str(budget.path.resolve()).encode()).hexdigest(),
        'before_report': {'path': before_path.relative_to(path.parent).as_posix(), 'sha256': sha256(before_path)},
        'runner_source': {'path': runner_path.relative_to(path.parent).as_posix(), 'sha256': sha256(runner_path)}})
    save(path, report)


def run_cases(site, index, dataset, report, budget, output, start_index=0):
    original_post = site.post_json
    for case in dataset['cases'][start_index:]:
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
            save(output, report)  # Persist attempt before network, including failure.
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
        save(output, report)
        print(case['id'], row['status'], report['provider_requests'], '/ 24', flush=True)
        if row['status'] == 'error' and row.get('http_status') != 502:
            return 1
    print(json.dumps(report['summary'], sort_keys=True))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--budget-ledger', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--verify-report', type=Path)
    parser.add_argument('--resume', action='store_true', help='Continue only unattempted cases using the existing report and original ledger')
    args = parser.parse_args(argv)
    if args.verify_report:
        if args.resume:
            parser.error('--verify-report cannot be combined with --resume')
        print(json.dumps(verify(args.verify_report), sort_keys=True))
        return 0
    if not args.live or not args.output or not args.budget_ledger or not args.env_file:
        parser.error('Live runs require explicit --live, private --env-file, --budget-ledger and new --output')
    if not args.output.parent.is_dir() or (args.output.exists() and not args.resume):
        parser.error('Output must be a new file in an existing directory')
    from evaluation.broader import validate_bundle
    validate_bundle(require_review=True)  # Frozen reviewed source labels, before paid work.
    dataset, corpus = load_dataset(ROOT / 'evaluation/broader_v1/heldout.json', ROOT / 'evaluation/broader_v1/corpus.json')
    if len(dataset['cases']) != 24:
        parser.error('Expected frozen 24-case heldout sample')
    report, budget, start_index = None, None, 0
    if args.resume:
        try:
            report, budget, start_index = prepare_resume(args.output, args.budget_ledger)
        except (ValueError, OSError, KeyError, TypeError):
            parser.error('Resume validation failed; existing report, snapshots and original ledger must agree. No requests sent.')
    from dotenv import dotenv_values
    values = dotenv_values(args.env_file)
    key = values.get('OPENAI_API_KEY') or values.get('OPEN_AI_API_KEY')
    if not key:
        parser.error('Private provider credential unavailable; no requests sent')
    os.environ.update(OPENAI_API_KEY=key, OPENAI_MODEL='gpt-6-luna', OPENAI_MAX_COMPLETION_TOKENS='800')
    if os.getenv('STAGING_WORKLOAD_ENABLED') == '1':
        parser.error('Refuse live evaluation with staging fixtures enabled')
    if budget is None:
        budget = RequestBudget(args.budget_ledger, authorization=AUTHORIZATION, limit=24, output_cap=800)
        if budget.snapshot()['attempts']:
            parser.error('This allowance already has attempts; explicit --resume with its original report is required')
    sys.path.insert(0, str(ROOT / 'web'))
    site = importlib.import_module('app')
    from web.engine import Index
    index = Index(chunks=corpus)
    if args.resume:
        attach_continuation(args.output, report, budget)
        return run_cases(site, index, dataset, report, budget, args.output, start_index)
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
    return run_cases(site, index, dataset, report, budget, args.output)


if __name__ == '__main__':
    raise SystemExit(main())
