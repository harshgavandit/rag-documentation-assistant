"""Closed preview-only provider fixtures; never sends an outbound model request."""
from contextvars import ContextVar
import hashlib
import hmac
from importlib.metadata import version
import json
import os
from pathlib import Path
import re
import sys
import time

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent
SCENARIOS = frozenset(('valid', 'invalid_citation', 'provider_error', 'missing_usage', 'truncated', 'refusal', 'slow'))
SOURCE_FILES = ('app.py', 'staging.py', 'engine.py', 'grounding.py', 'observability.py', 'prepare.py',
                'data/corpus.json', 'model_manifest.json', 'requirements.txt', 'vercel.json')
_scenario = ContextVar('staging_scenario', default=None)


def settings():
    """Require explicit preview, disposable namespaces, dummy key and distinct controls."""
    enabled = os.getenv('STAGING_WORKLOAD_ENABLED', '')
    if enabled in ('', '0'):
        return None
    if enabled != '1':
        raise HTTPException(503, 'Staging workload configuration is unavailable.')
    run = os.getenv('STAGING_RUN_ID', '')
    source = os.getenv('STAGING_SOURCE_SHA', '')
    token = os.getenv('STAGING_WORKLOAD_TOKEN', '')
    ops_token = os.getenv('OPS_READ_TOKEN', '')
    valid = (os.getenv('VERCEL_ENV') == 'preview' and os.getenv('VERCEL') == '1'
             and re.fullmatch(r'[a-f0-9]{12,32}', run) and re.fullmatch(r'[a-f0-9]{40}', source)
             and 32 <= len(token) <= 512 and 32 <= len(ops_token) <= 512 and token != ops_token
             and os.getenv('OPENAI_API_KEY') == 'staging-unused-no-provider'
             and os.getenv('OPENAI_MODEL') == 'deterministic-fixture-v1'
             and os.getenv('RATE_LIMIT_PREFIX') == f'docwise:staging:{run}:admission'
             and os.getenv('OPS_PREFIX') == f'docwise:staging:{run}:operations'
             and os.getenv('RATE_LIMIT_REQUESTS') == '20' and os.getenv('DAILY_QUERY_LIMIT') == '9'
             and os.getenv('OPS_ENABLED') == '1'
             and os.getenv('OPS_PROMPT_USD_PER_MILLION') == '1'
             and os.getenv('OPS_OUTPUT_USD_PER_MILLION') == '2')
    if not valid:
        raise HTTPException(503, 'Staging workload configuration is unavailable.')
    return {'run_id': run, 'source_sha': source, 'token': token}


def authorize(request, *, provenance=False):
    config = settings()
    if config is None:
        if provenance:
            raise HTTPException(404, 'Not found.')
        return None
    supplied = request.headers.get('x-staging-token', '')
    if len(supplied) > 512 or not hmac.compare_digest(supplied.encode(), config['token'].encode()):
        raise HTTPException(403, 'Staging workload authorization is required.')
    if provenance:
        return config
    # Every admitted staging query must also enter the separate synthetic cohort.
    ops_supplied = request.headers.get('x-ops-token', '')
    if len(ops_supplied) > 512 or not hmac.compare_digest(ops_supplied.encode(), os.environ['OPS_READ_TOKEN'].encode()):
        raise HTTPException(403, 'Synthetic operations authorization is required.')
    scenario = request.headers.get('x-staging-scenario', '')
    if scenario not in SCENARIOS:
        raise HTTPException(422, 'Select an allowed staging workload scenario.')
    return scenario


def enter(scenario):
    return _scenario.set(scenario)


def leave(token):
    _scenario.reset(token)


def response(chunks, refusal):
    """Inject only the provider result; admission, retrieval and answer guards remain real."""
    if settings() is None:
        return None
    scenario = _scenario.get()
    if scenario not in SCENARIOS:
        raise HTTPException(503, 'Staging request context is unavailable.')
    if scenario == 'provider_error':
        raise HTTPException(503, 'Injected staging provider failure.')
    if scenario == 'slow':
        time.sleep(.25)
    if not chunks:
        text = refusal
    else:
        metadata = chunks[0]['metadata']
        text = (f"Deterministic fixture [Source: {metadata['doc_title']}, Section: {metadata['section_title']}]")
    if scenario == 'invalid_citation':
        text = 'Deterministic fixture [Source: absent-fixture.md, Section: absent-fixture]'
    elif scenario == 'refusal':
        text = refusal
    result = {'choices': [{'finish_reason': 'length' if scenario == 'truncated' else 'stop',
                           'message': {'content': text}}]}
    if scenario != 'missing_usage':
        result['usage'] = {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120}
    return result


def source_hashes(root=ROOT):
    return {name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in SOURCE_FILES}


def provenance(request):
    config = authorize(request, provenance=True)
    manifest = json.loads((ROOT/'model_manifest.json').read_text(encoding='utf-8'))
    artifact_hashes = {}
    for name, info in manifest['files'].items():
        path = ROOT/'models'/name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != info['sha256']:
            raise HTTPException(503, 'Staging embedding artifacts are unavailable.')
        artifact_hashes[name] = info['sha256']
    deployment_host = os.getenv('VERCEL_URL', '')
    deployment_id = os.getenv('VERCEL_DEPLOYMENT_ID', '')
    if not re.fullmatch(r'[a-z0-9-]+\.vercel\.app', deployment_host) or not re.fullmatch(r'dpl_[A-Za-z0-9]{8,80}', deployment_id):
        raise HTTPException(503, 'Staging deployment identity is unavailable.')
    return {'schema_version': 1, 'environment': 'preview', 'run_id': config['run_id'],
            'deployment_host': deployment_host, 'deployment_id': deployment_id,
            'source_sha': config['source_sha'], 'source_sha256': source_hashes(),
            'embedding_artifacts_sha256': artifact_hashes,
            'provider_mode': 'deterministic-injected-no-network',
            'model': 'deterministic-fixture-v1', 'provider_requests': 0,
            'rate_limit_prefix': os.environ['RATE_LIMIT_PREFIX'], 'operations_prefix': os.environ['OPS_PREFIX'],
            'limits': {'global_per_minute': 20, 'client_per_minute': 3, 'daily_admissions': 9},
            'fixture_usage': {'prompt_tokens': 100, 'completion_tokens': 20},
            'runtime_versions': {'python': '.'.join(map(str, sys.version_info[:3])),
                                 **{name: version(name) for name in ('fastapi', 'numpy', 'onnxruntime', 'tokenizers', 'rank-bm25')}},
            'fixture_rates_usd_per_million': {'prompt': '1', 'output': '2'}}
