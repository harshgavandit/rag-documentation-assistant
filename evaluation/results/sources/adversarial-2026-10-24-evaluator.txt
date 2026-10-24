"""Opt-in, bounded synthetic-context challenge run through production answer()."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from time import perf_counter
from unittest.mock import patch

from evaluation.run import ROOT, sha256
from evaluation.evaluate_answers import usage_fields

DATASET = ROOT / 'evaluation/adversarial_cases.json'


def load_cases():
    data = json.loads(DATASET.read_text(encoding='utf-8-sig'))
    ids = set()
    if data['schema_version'] != 1 or len(data['cases']) != 12:
        raise ValueError('Expected the frozen twelve-case challenge set')
    for case in data['cases']:
        if case['id'] in ids or not case['question'].strip() or not 1 <= len(case['chunks']) <= 5:
            raise ValueError('Invalid case identity or context size')
        ids.add(case['id'])
        labels = set()
        for rank, chunk in enumerate(case['chunks'], 1):
            if chunk['rank'] != rank or not chunk['content'].strip():
                raise ValueError('Invalid context rank or content')
            labels.add(f"[Source: {chunk['metadata']['doc_title']}, Section: {chunk['metadata']['section_title']}]")
        grading = case['grading']
        if not set(grading['required_citations']).issubset(labels):
            raise ValueError('Unknown required citation')
        if grading['decision'] not in ('answer', 'clarify_or_refuse', 'refuse'):
            raise ValueError('Unknown decision policy')
        for group in ('required_patterns', 'forbidden_patterns'):
            for pattern in grading[group]:
                re.compile(pattern, re.I)
    return data


def score_case(case, result):
    """Observable regex/label proxies, not semantic or universal safety judgments."""
    answer = result['answer']
    grading = case['grading']
    allowed = {f"[Source: {c['metadata']['doc_title']}, Section: {c['metadata']['section_title']}]" for c in case['chunks']}
    citations = result.get('citations', [])
    refused = result['refused']
    required = [bool(re.search(p, answer, re.I)) for p in grading['required_patterns']]
    forbidden = [bool(re.search(p, answer, re.I)) for p in grading['forbidden_patterns']]
    labels_valid = bool(citations) and all(c in allowed for c in citations)
    required_labels = set(grading['required_citations']).issubset(citations)
    if grading['decision'] in ('clarify_or_refuse', 'refuse') and refused:
        decision = True
        positive = True
        citation_check = True
    else:
        decision = not refused and grading['decision'] != 'refuse'
        positive = all(required)
        citation_check = labels_valid and required_labels
    return {'refused': refused, 'decision_matches_policy': decision,
            'required_pattern_matches': required, 'forbidden_pattern_matches': forbidden,
            'non_refusal_labels_valid': labels_valid if not refused else None,
            'required_citations_present': required_labels if not refused else None,
            'fixture_pass': decision and positive and not any(forbidden) and citation_check}


def summarize(records):
    done = [r for r in records if r['status'] == 'completed']
    keys = ('prompt_tokens', 'completion_tokens', 'total_tokens')
    usage = [r.get('usage') or {} for r in records]
    return {'attempted_cases': len(records), 'completed_cases': len(done),
            'error_cases': len(records) - len(done),
            'fixture_passed': sum(r['checks']['fixture_pass'] for r in done),
            'records_with_complete_usage': sum(all(u.get(k) is not None for k in keys) for u in usage),
            'token_totals': {k: sum(u[k] for u in usage) if usage and all(u.get(k) is not None for u in usage) else None for k in keys},
            'known_token_subtotals': {k: sum(u[k] for u in usage if u.get(k) is not None) for k in keys},
            'currency_cost': None}


def save(path, report):
    report['summary'] = summarize(report['cases'])
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, suffix='.tmp', delete=False) as output:
        temporary = Path(output.name)
        json.dump(report, output, ensure_ascii=False, indent=2, allow_nan=False)
        output.write('\n')
    temporary.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--max-requests', type=int, default=12)
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.live:
        parser.error('No requests sent. Explicit --live is required.')
    if not 1 <= args.max_requests <= 12:
        parser.error('Request budget must be between 1 and 12; no requests sent.')
    if args.output.suffix != '.json' or not args.output.parent.is_dir() or args.output.exists():
        parser.error('Output must be a new .json file in an existing directory; no requests sent.')
    data = load_cases()
    if len(data['cases']) > args.max_requests:
        parser.error('Frozen sample exceeds the request budget; no requests sent.')
    if args.env_file:
        from dotenv import dotenv_values
        values = dotenv_values(args.env_file)
        for name in ('OPENAI_API_KEY', 'OPENAI_MODEL', 'OPENAI_MAX_COMPLETION_TOKENS'):
            if values.get(name) and not os.getenv(name):
                os.environ[name] = values[name]
    if not os.getenv('OPENAI_API_KEY', '').strip():
        parser.error('An existing OPENAI_API_KEY is required; no requests sent.')
    sys.path.insert(0, str(ROOT / 'web'))
    site = importlib.import_module('app')
    report = {'schema_version': 1, 'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
              'scope': data['scope'], 'dataset_id': data['dataset_id'], 'dataset_sha256': sha256(DATASET),
              'source_sha256': {p: sha256(ROOT / p) for p in ('evaluation/adversarial.py', 'web/grounding.py', 'web/app.py')},
              'requested_model': os.getenv('OPENAI_MODEL', 'gpt-6-luna'),
              'max_completion_tokens': int(os.getenv('OPENAI_MAX_COMPLETION_TOKENS', '2048')),
              'request_budget': args.max_requests, 'provider_requests': 0,
              'grading': data['grading_limitations'], 'cases': []}
    with args.output.open('x', encoding='utf-8') as output:
        output.write('{"status":"in_progress","provider_requests":0}\n')
    save(args.output, report)
    original = site.post_json
    for case in data['cases']:
        captured = {}
        started = perf_counter()
        def measured_post(url, payload, token, timeout=15):
            if url != 'https://api.openai.com/v1/chat/completions' or report['provider_requests'] >= args.max_requests:
                raise RuntimeError('Unexpected provider destination or exhausted request budget')
            report['provider_requests'] += 1
            try:
                response = original(url, payload, token, timeout)
                choice = (response.get('choices') or [{}])[0]
                captured.update(usage=usage_fields(response), model=response.get('model'),
                                provider_output=(choice.get('message') or {}).get('content'), finish_reason=choice.get('finish_reason'))
                return response
            finally:
                captured['provider_elapsed_ms'] = round((perf_counter() - started) * 1000, 3)
        row = {'id': case['id'], 'category': case['category'], 'question': case['question'],
               'context_kind': 'authored_synthetic_fixture', 'chunks': case['chunks']}
        try:
            with patch.object(site, 'post_json', measured_post):
                result = site.answer(case['question'], case['chunks'])
            row.update(status='completed', result=result, checks=score_case(case, result))
        except Exception as error:
            row.update(status='error', error_type=type(error).__name__)
            if isinstance(getattr(error, 'status_code', None), int):
                row['http_status'] = error.status_code
        row.update(captured)
        row['elapsed_ms'] = round((perf_counter() - started) * 1000, 3)
        report['cases'].append(row)
        save(args.output, report)
        print(f"{case['id']}: {row['status']} ({report['provider_requests']}/{args.max_requests} provider requests)", flush=True)
    return 0 if report['summary']['error_cases'] == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
