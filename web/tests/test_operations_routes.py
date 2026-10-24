"""HTTP/usage integration with deterministic services; no external calls."""
from pathlib import Path
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
import app as site


class OperationsRoutesTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(site.app)
        self.chunk = {'content': 'A fact', 'rank': 1, 'score': .1, 'metadata': {'doc_title': 'a.md', 'section_title': 'Tools'}}

    def test_private_snapshot_auth_and_disabled_state(self):
        with patch.object(site.ops, 'is_enabled', return_value=False):
            self.assertEqual(self.client.get('/api/operations').status_code, 404)
        with patch.object(site.ops, 'is_enabled', return_value=True), patch.object(site.ops, 'is_authorized', return_value=False), patch.object(site.ops, 'snapshot') as snapshot:
            self.assertEqual(self.client.get('/api/operations').status_code, 401)
            snapshot.assert_not_called()
        with patch.object(site.ops, 'is_enabled', return_value=True), patch.object(site.ops, 'is_authorized', return_value=True), patch.object(site.ops, 'snapshot', return_value={'totals': {}}):
            response = self.client.get('/api/operations', headers={'x-ops-token': 'secret'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['cache-control'], 'no-store')
            self.assertEqual(self.client.get('/api/operations?days=8').status_code, 422)

    def test_feedback_rejects_free_text_and_preserves_atomic_service_status(self):
        payload = {'ticket': 'a'*43, 'helpful': True, 'category': 'clear'}
        with patch.object(site.ops, 'is_enabled', return_value=True), patch.object(site.ops, 'feedback', return_value={'status': 'accepted'}) as save:
            self.assertEqual(self.client.post('/api/feedback', json=dict(payload, message='do not store')).status_code, 422)
            self.assertEqual(self.client.post('/api/feedback', json=dict(payload, helpful='yes')).status_code, 422)
            save.assert_not_called()
            self.assertEqual(self.client.post('/api/feedback', json=payload).status_code, 200)
        for status, expected in [('expired_or_used', 409), ('limited', 429), ('unavailable', 503)]:
            with patch.object(site.ops, 'is_enabled', return_value=True), patch.object(site.ops, 'feedback', return_value={'status': status}):
                self.assertEqual(self.client.post('/api/feedback', json=payload).status_code, expected)

    def test_failure_event_is_finished_and_provider_work_not_called(self):
        with patch.object(site, 'configuration'), patch.object(site.ops, 'begin_request', return_value='event') as begin, patch.object(site.ops, 'finish_request') as finish, patch.object(site, 'admit', side_effect=site.HTTPException(429, 'limited')), patch.object(site, 'answer') as provider:
            self.assertEqual(self.client.post('/api/query', json={'question': 'How?'}, headers={'x-ops-token': 'signed'}).status_code, 429)
            begin.assert_called_once_with('signed')
            finish.assert_called_once_with('event', status_code=429, outcome='error')
            provider.assert_not_called()

    def test_successful_query_returns_opaque_ticket(self):
        with patch.object(site, 'configuration'), patch.object(site, 'admit'), patch.object(site, 'get_index') as index, patch.object(site, 'answer', return_value={'answer': 'Fact', 'citations': [], 'refused': False}), patch.object(site.ops, 'begin_request', return_value='event'), patch.object(site.ops, 'finish_request', return_value='a'*43) as finish:
            index.return_value.search.return_value = [self.chunk]
            response = self.client.post('/api/query', json={'question': 'How?'})
            self.assertEqual(response.json()['feedback_ticket'], 'a'*43)
            finish.assert_called_once_with('event', status_code=200, outcome='answered')

    def test_actual_provider_usage_recorded_before_citation_rejection(self):
        usage = {'prompt_tokens': 10, 'completion_tokens': 3}
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test'}), patch.object(site, 'post_json', return_value={'usage': usage, 'choices': [{'finish_reason': 'stop', 'message': {'content': 'Uncited response'}}]}), patch.object(site.ops, 'current_event', return_value='event'), patch.object(site.ops, 'record_usage') as record:
            with self.assertRaises(site.HTTPException):
                site.answer('question', [self.chunk])
            record.assert_called_once_with('event', usage)
