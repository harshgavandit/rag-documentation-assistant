"""Live verification: real Redis, local embeddings/Chroma and configured OpenAI generation.

Uses uniquely named expiring Redis keys. Does not reset databases or replace credentials.
"""
import contextlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings
from pipeline import graph
from pipeline.health import check_health
from pipeline.rate_limit import RateLimitExceeded, enforce_rate_limit, redis_command


def main() -> int:
    """Write a credential-safe report and fail if any live invariant fails."""
    report = {'checked_at_utc': datetime.now(timezone.utc).isoformat(),
              'model': settings.OPENAI_MODEL, 'checks': {}, 'answers': []}
    checks = report['checks']
    settings.validate_configuration()
    prefix = 'rag:verification:' + uuid.uuid4().hex
    settings.RATE_LIMIT_PREFIX = prefix
    try:
        health = check_health(live=True)
        checks['readiness'] = health
        if health['status'] != 'PASS':
            raise RuntimeError('Readiness failed; consult individual checks.')
        with patch.object(settings, 'RATE_LIMIT_REQUESTS', 3), patch.object(settings, 'RATE_LIMIT_WINDOW_SECONDS', 10):
            def attempt(_: int) -> bool:
                """Perform one independent Redis admission request."""
                try:
                    enforce_rate_limit()
                    return True
                except RateLimitExceeded:
                    return False
            with ThreadPoolExecutor(max_workers=10) as executor:
                outcomes = list(executor.map(attempt, range(10)))
            if outcomes.count(True) != 3 or outcomes.count(False) != 7:
                raise RuntimeError('Atomic rate-limit admission invariant failed.')
            checks['redis_concurrency'] = {'status': 'PASS', 'attempts': 10, 'admitted': 3, 'denied': 7}
            # A new Python process must see the same exhausted quota.
            script = ('from config import settings; from pipeline.rate_limit import enforce_rate_limit, RateLimitExceeded; '
                      f'settings.RATE_LIMIT_PREFIX={prefix!r}; settings.RATE_LIMIT_REQUESTS=3; '
                      'settings.RATE_LIMIT_WINDOW_SECONDS=10\n'
                      'try:\n enforce_rate_limit(); raise SystemExit(1)\n'
                      'except RateLimitExceeded:\n print("shared-quota-denied")')
            child = subprocess.run([sys.executable, '-c', script], cwd=settings.PROJECT_ROOT,
                                   capture_output=True, text=True, timeout=8)
            if child.returncode != 0 or 'shared-quota-denied' not in child.stdout:
                raise RuntimeError('Cross-process shared rate limit failed.')
            checks['redis_cross_process'] = {'status': 'PASS', 'second_process_denied': True}
            with patch.object(graph, 'build_rag_graph') as build:
                try:
                    graph.run_query('This request must be blocked before retrieval.')
                except RateLimitExceeded as exc:
                    delay = exc.retry_after_seconds
                else:
                    raise RuntimeError('Pipeline bypassed the exhausted rate limiter.')
                build.assert_not_called()
            checks['denial_before_model'] = {'status': 'PASS', 'retry_after_seconds': delay}
            time.sleep(delay + 0.2)
            enforce_rate_limit()
            checks['redis_expiry'] = {'status': 'PASS', 'admitted_after_expiry': True}
        with patch.object(settings, 'UPSTASH_REDIS_REST_TOKEN', 'invalid-verification-token'):
            try:
                redis_command(['PING'])
            except RuntimeError as exc:
                if 'HTTP 401' not in str(exc):
                    raise RuntimeError('Unexpected Redis authentication failure behavior.')
            else:
                raise RuntimeError('Invalid Redis token unexpectedly accepted.')
        checks['redis_invalid_credentials'] = {'status': 'PASS', 'http_status': 401}
        # Separate query namespace from the small concurrency-test window.
        settings.RATE_LIMIT_PREFIX = prefix + ':queries'
        from pipeline.graph import run_query
        for question, expected in [
            ('How does FastMCP register a tool with the MCP server?', 'cited'),
            ('What is the rate limit on the Stripe API?', 'refusal'),
        ]:
            print('Testing generation: ' + expected, flush=True)
            with contextlib.redirect_stdout(io.StringIO()):
                answer = run_query(question)
            report['answers'].append(asdict(answer))
            if expected == 'cited':
                if not answer.citations or not any('MCP_Documentation.md' in c and '7.' in c for c in answer.citations):
                    raise RuntimeError('In-scope answer did not cite the required FastMCP section.')
            elif answer.text != settings.REFUSAL_TEXT or answer.citations:
                raise RuntimeError('Out-of-scope answer did not refuse exactly.')
            checks['generation_' + expected] = {'status': 'PASS', 'chunks_used': answer.chunks_used,
                                                'citations': len(answer.citations)}
        report['status'] = 'PASS'
    except Exception as exc:
        report['status'] = 'FAIL'
        report['failure'] = str(exc) if isinstance(exc, (RuntimeError, ValueError)) else type(exc).__name__
    output = settings.PROJECT_ROOT / 'verification' / 'live_report.json'
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'status': report['status'], 'report': str(output), 'checks': checks}, indent=2))
    if report['status'] != 'PASS':
        print(report['failure'])
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
