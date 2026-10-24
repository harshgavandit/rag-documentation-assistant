"""Preview workload controls must never create a public/production provider bypass."""
from contextlib import ExitStack
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
import app as site

RUN = '0123456789abcdef'
TOKEN = 'staging-control-' + 's'*40
OPS_TOKEN = 'operations-control-' + 'o'*40
ENV = {'VERCEL_ENV': 'preview', 'VERCEL': '1', 'STAGING_WORKLOAD_ENABLED': '1',
       'VERCEL_URL': 'docwise-abc123456-testteam.vercel.app', 'VERCEL_DEPLOYMENT_ID': 'dpl_abcdefghijklmnop',
       'STAGING_WORKLOAD_TOKEN': TOKEN, 'STAGING_RUN_ID': RUN, 'STAGING_SOURCE_SHA': 'a'*40,
       'OPENAI_API_KEY': 'staging-unused-no-provider', 'OPENAI_MODEL': 'deterministic-fixture-v1',
       'UPSTASH_REDIS_REST_URL': 'https://redis.example', 'UPSTASH_REDIS_REST_TOKEN': 'redis-test',
       'RATE_LIMIT_PREFIX': f'docwise:staging:{RUN}:admission', 'RATE_LIMIT_REQUESTS': '20',
       'DAILY_QUERY_LIMIT': '9', 'OPS_ENABLED': '1', 'OPS_READ_TOKEN': OPS_TOKEN,
       'OPS_PREFIX': f'docwise:staging:{RUN}:operations',
       'OPS_PROMPT_USD_PER_MILLION': '1', 'OPS_OUTPUT_USD_PER_MILLION': '2'}
CHUNK = {'content': 'A retrieved fact.', 'rank': 1, 'score': .03,
         'metadata': {'doc_title': 'a.md', 'section_title': 'Tools'}}


class StagingRoutesTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, ENV))
        self.client = TestClient(site.app)

    def request(self, scenario='valid', token=TOKEN):
        return self.client.post('/api/query', json={'question': 'How do tools work?'},
                                headers={'x-staging-token': token, 'x-staging-scenario': scenario,
                                         'x-ops-token': OPS_TOKEN})

    def test_unsigned_preview_is_denied_before_admission(self):
        with patch.object(site, 'admit', side_effect=AssertionError('admission reached')):
            self.assertEqual(self.request(token='').status_code, 403)

    def test_production_flag_fails_closed_before_admission(self):
        with patch.dict(os.environ, {'VERCEL_ENV': 'production'}), patch.object(site, 'admit', side_effect=AssertionError('admission reached')):
            self.assertEqual(self.request().status_code, 503)

    def test_malformed_enablement_cannot_fall_through_to_real_provider(self):
        with patch.dict(os.environ, {'STAGING_WORKLOAD_ENABLED': 'yes'}), patch.object(site, 'admit', side_effect=AssertionError('admission reached')):
            self.assertEqual(self.request().status_code, 503)

    def test_shared_namespace_and_real_provider_key_are_rejected(self):
        for values in ({'RATE_LIMIT_PREFIX': 'rag:web:v1'}, {'OPS_PREFIX': 'docwise:operations:v1'},
                       {'OPENAI_API_KEY': 'a-real-looking-key'}, {'STAGING_WORKLOAD_TOKEN': OPS_TOKEN}):
            with patch.dict(os.environ, values), patch.object(site, 'admit', side_effect=AssertionError('admission reached')):
                self.assertEqual(self.request().status_code, 503)

    def test_unknown_scenario_cannot_select_arbitrary_work(self):
        with patch.object(site, 'admit', side_effect=AssertionError('admission reached')):
            self.assertEqual(self.request('https://untrusted.example').status_code, 422)

    def test_deterministic_scenarios_use_existing_answer_guards_without_provider_transport(self):
        def redis_only(url, payload, token, timeout=15):
            if url != 'https://redis.example':
                raise AssertionError('Outbound provider transport attempted')
            return {'result': [1, 2, 60000]}
        with patch.object(site, 'post_json', side_effect=redis_only), patch.object(site, 'get_index') as index, patch.object(site.ops, '_command', return_value=1):
            index.return_value.search.return_value = [CHUNK]
            for scenario, status in [('valid', 200), ('invalid_citation', 502), ('provider_error', 503),
                                     ('missing_usage', 200), ('truncated', 502), ('refusal', 200), ('slow', 200)]:
                response = self.request(scenario)
                self.assertEqual(response.status_code, status, scenario)
                if status == 200:
                    self.assertEqual(response.json()['model'], 'deterministic-fixture-v1')
                    self.assertEqual(response.json()['sources'][0]['document'], 'a.md')
                    self.assertEqual(response.json().get('staging_admission'), {'client_ttl_ms': 60000})

    def test_staging_headers_cannot_change_normal_production_answers(self):
        with patch.dict(os.environ, {'VERCEL_ENV': 'production', 'STAGING_WORKLOAD_ENABLED': '0'}), patch.object(site, 'admit'), patch.object(site, 'get_index') as index, patch.object(site, 'answer', return_value={'answer': 'normal', 'citations': [], 'refused': False}):
            index.return_value.search.return_value = [CHUNK]
            self.assertEqual(self.request('invalid_citation').json()['answer'], 'normal')

    def test_provenance_is_private_and_exposes_no_controls(self):
        self.assertEqual(self.client.get('/api/staging/provenance').status_code, 403)
        response = self.client.get('/api/staging/provenance', headers={'x-staging-token': TOKEN})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['source_sha'], 'a'*40)
        self.assertEqual(body['run_id'], RUN)
        self.assertEqual(body['provider_mode'], 'deterministic-injected-no-network')
        self.assertEqual(body['deployment_host'], 'docwise-abc123456-testteam.vercel.app')
        self.assertEqual(body['deployment_id'], 'dpl_abcdefghijklmnop')
        self.assertIn('python', body['runtime_versions'])
        self.assertIn('prepare.py', body['source_sha256'])
        self.assertIn('app.py', body['source_sha256'])
        self.assertNotIn(TOKEN, response.text)
        self.assertNotIn(OPS_TOKEN, response.text)
        with patch.dict(os.environ, {'STAGING_WORKLOAD_ENABLED': '0'}):
            self.assertEqual(self.client.get('/api/staging/provenance', headers={'x-staging-token': TOKEN}).status_code, 404)
