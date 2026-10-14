"""Credential-safe configuration, storage and service diagnostics."""
from pathlib import Path
from config import settings
from pipeline.rate_limit import redis_command


def check_health(live: bool = False) -> dict:
    """Return separate readiness checks; live mode pings Redis and checks model access."""
    result = {'model': settings.OPENAI_MODEL, 'checks': {}}
    checks = result['checks']
    try:
        settings.validate_configuration()
        checks['configuration'] = {'status': 'PASS'}
    except ValueError as exc:
        checks['configuration'] = {'status': 'FAIL', 'detail': str(exc)}
    docs = list(Path(settings.DOCS_PATH).glob('*.md'))
    checks['documents'] = {'status': 'PASS' if docs else 'FAIL', 'count': len(docs)}
    try:
        from vectorstore.store import load_collection
        collection = load_collection(settings.CHROMA_PATH, settings.COLLECTION_NAME)
        count = collection.count()
        checks['vector_store'] = {'status': 'PASS' if count else 'FAIL', 'chunks': count}
    except Exception as exc:
        checks['vector_store'] = {'status': 'FAIL', 'detail': type(exc).__name__,
                                  'action': 'Run python main.py ingest.'}
    if live and checks['configuration']['status'] == 'PASS':
        try:
            checks['redis'] = {'status': 'PASS' if redis_command(['PING']) == 'PONG' else 'FAIL'}
        except RuntimeError as exc:
            checks['redis'] = {'status': 'FAIL', 'detail': str(exc)}
        try:
            from openai import OpenAI
            with OpenAI(api_key=settings.OPENAI_API_KEY, timeout=settings.OPENAI_TIMEOUT_SECONDS,
                        max_retries=0) as client:
                model = client.models.retrieve(settings.OPENAI_MODEL)
            checks['model_access'] = {'status': 'PASS', 'model': model.id}
        except Exception as exc:
            checks['model_access'] = {'status': 'FAIL', 'detail': type(exc).__name__,
                                      'http_status': getattr(exc, 'status_code', None)}
    result['status'] = 'PASS' if all(c['status'] == 'PASS' for c in checks.values()) else 'FAIL'
    result['scope'] = 'Live connectivity and local readiness; no generation call.' if live else 'Local readiness only.'
    return result
