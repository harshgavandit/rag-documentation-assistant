"""Opt-in Redis/HTTP feedback and isolation drill; zero model requests."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'web'))
from observability import Operations


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def http(url, token=None, data=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['x-ops-token'] = token
    request = Request(url, data=None if data is None else json.dumps(data).encode(), headers=headers)
    try:
        with build_opener(NoRedirect).open(request, timeout=20) as response:
            return response.status, json.loads(response.read(65536))
    except HTTPError as error:
        return error.code, None
    except Exception:
        return None, None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--base-url', default='https://rag-documentation-assistant.vercel.app')
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--control-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.live:
        parser.error('No requests sent. --live is required.')
    if args.output.suffix != '.json' or args.output.exists() or not args.output.parent.is_dir():
        parser.error('Output must be a new JSON file in an existing directory.')
    parsed = urlparse(args.base_url)
    if parsed.scheme != 'https' or parsed.path or parsed.query or parsed.fragment or parsed.username or not parsed.hostname:
        parser.error('Base URL must be an HTTPS origin.')
    from dotenv import dotenv_values
    credentials, control = dotenv_values(args.env_file), dotenv_values(args.control_file)
    values = {key: credentials.get(key, '') for key in ('UPSTASH_REDIS_REST_URL', 'UPSTASH_REDIS_REST_TOKEN')}
    values.update(OPS_READ_TOKEN=control.get('OPS_READ_TOKEN', ''), OPS_ENABLED='1', OPS_PREFIX='docwise:operations:v1')
    ops = Operations(values)
    token = values['OPS_READ_TOKEN']
    if not ops.is_authorized(token) or not all(values[key] for key in ('UPSTASH_REDIS_REST_URL', 'UPSTASH_REDIS_REST_TOKEN')):
        parser.error('Existing private Redis and operations credentials are required.')
    report = {'schema_version': 1, 'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
              'scope': 'Controlled signed synthetic events written by the actual local Operations adapter to the deployed shared Redis namespace, then real deployed feedback/reader HTTP checks. No query endpoint, answer generation, actual visitor feedback or production-user outcome is exercised.',
              'provider_requests': 0, 'target_host': parsed.hostname, 'observations': [],
              'source_sha256': {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in ('web/observability.py', 'operations/verify_live.py')}}
    def observe(name, passed, **safe):
        report['observations'].append(dict(name=name, passed=bool(passed), **safe))
    with args.output.open('x', encoding='utf-8') as output:
        output.write('{"status":"in_progress"}\n')
    status, _ = http(args.base_url+'/api/operations')
    observe('unauthorized_reader_denied', status == 401, http_status=status)
    status, before = http(args.base_url+'/api/operations?days=1', token)
    observe('authorized_reader_ready', status == 200 and isinstance(before, dict), http_status=status)
    if status == 200:
        # Deliberate synthetic adapter fixtures: no user question or model answer.
        event = ops.begin_request(token)
        ticket = ops.finish_request(event, status_code=200, outcome='answered')
        observe('signed_synthetic_fixture_ticket_issued', bool(ticket))
        if ticket:
            payload = {'ticket': ticket, 'helpful': True, 'category': 'clear'}
            first, _ = http(args.base_url+'/api/feedback', data=payload)
            replay, _ = http(args.base_url+'/api/feedback', data=payload)
            observe('real_deployed_feedback_accepts_once', first == 200 and replay == 409, first_status=first, replay_status=replay)
        event = ops.begin_request(token)
        expiring = ops.finish_request(event, status_code=200, outcome='answered')
        if expiring:
            # Shorten only this exact synthetic ticket's TTL; no application-key scan/deletion.
            key = 'docwise:operations:v1:ticket:'+hashlib.sha256(expiring.encode()).hexdigest()
            shortened = ops._command(['EXPIRE', key, 1])
            time.sleep(1.2)
            expired, _ = http(args.base_url+'/api/feedback', data={'ticket': expiring, 'helpful': False, 'category': 'other'})
            observe('real_ticket_expiry_rejected', shortened == 1 and expired == 409, http_status=expired)
        else:
            observe('real_ticket_expiry_rejected', False)
        status, after = http(args.base_url+'/api/operations?days=1', token)
        if status == 200:
            old, new = before['totals']['synthetic'], after['totals']['synthetic']
            observe('signed_fixtures_stay_synthetic', new['queries']-old['queries'] == 2 and new['feedback_total']-old['feedback_total'] == 1,
                    synthetic_event_delta=new['queries']-old['queries'], synthetic_feedback_delta=new['feedback_total']-old['feedback_total'])
            report['observed_visitor_query_delta'] = after['totals']['visitor']['queries']-before['totals']['visitor']['queries']
            report['visitor_delta_note'] = 'No visitor event is intentionally written by this drill. Concurrent unsigned activity is uncontrolled; the observed delta is not unique people or a product outcome.'
        else:
            observe('signed_fixtures_stay_synthetic', False, http_status=status)
    report['passed'] = len(report['observations']) == 6 and all(row['passed'] for row in report['observations'])
    args.output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'observations': report['observations'], 'provider_requests': 0}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
