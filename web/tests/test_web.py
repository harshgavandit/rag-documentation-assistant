"""HTTP boundary and public-demo controls, with no external calls."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
import app as site


class WebTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(site.app)
        self.env=patch.dict(os.environ,{'OPENAI_API_KEY':'test','UPSTASH_REDIS_REST_URL':'https://example.upstash.io','UPSTASH_REDIS_REST_TOKEN':'test'})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_static_and_library(self):
        response=self.client.get('/')
        self.assertEqual(response.status_code,200)
        self.assertIn('Good questions.',response.text)
        self.assertEqual(self.client.get('/assets/app.js').status_code,200)
        self.assertEqual(self.client.get('/assets/style.css').status_code,200)
        self.assertIn("default-src 'self'",response.headers['content-security-policy'])
        docs=self.client.get('/api/documents').json()['documents']
        self.assertEqual(sum(d['chunks'] for d in docs),39)

    def test_reject_empty_oversized_and_cross_origin(self):
        with patch.object(site,'admit') as admit:
            for text in ['','   ','a'*2001]:
                self.assertEqual(self.client.post('/api/query',json={'question':text}).status_code,422)
            self.assertEqual(self.client.post('/api/query',content='x'*9000).status_code,413)
            self.assertEqual(self.client.post('/api/query',json={'question':'test'},headers={'Origin':'https://untrusted.example'}).status_code,403)
            admit.assert_not_called()

    def test_query_success(self):
        chunk={'content':'facts','metadata':{'doc_title':'test.md','section_title':'Tools'},'rank':1,'score':.03}
        with patch.object(site,'admit'),patch.object(site,'get_index') as index,patch.object(site,'answer',return_value={'answer':'Facts','citations':[],'refused':False}):
            index.return_value.search.return_value=[chunk]
            response=self.client.post('/api/query',json={'question':'How?'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['sources'][0]['section'],'Tools')
        self.assertEqual(response.headers['cache-control'],'no-store')

    def test_rate_denial_stops_retrieval(self):
        with patch.object(site,'post_json',return_value={'result':[0,0,1501]}),patch.object(site,'get_index') as index:
            response=self.client.post('/api/query',json={'question':'How?'})
        self.assertEqual(response.status_code,429)
        self.assertEqual(response.headers['retry-after'],'2')
        index.assert_not_called()

    def test_redis_failure_is_closed(self):
        with patch.object(site,'post_json',return_value={'error':'bad secret'}),patch.object(site,'get_index') as index:
            response=self.client.post('/api/query',json={'question':'How?'})
        self.assertEqual(response.status_code,503)
        self.assertNotIn('bad secret',response.text)
        index.assert_not_called()

    def test_citation_guard_and_refusal(self):
        chunks=[{'content':'facts','metadata':{'doc_title':'a.md','section_title':'Tools'},'rank':1}]
        for content,valid in [('Facts [Source: a.md, Section: Tools]',True),(site.REFUSAL,True),('Facts [Source: fake.md, Section: fake]',False)]:
            with patch.object(site,'post_json',return_value={'choices':[{'finish_reason':'stop','message':{'content':content}}]}):
                if valid:
                    result=site.answer('test',chunks)
                    self.assertEqual(result['refused'],content==site.REFUSAL)
                else:
                    with self.assertRaises(site.HTTPException):site.answer('test',chunks)

    def test_credential_transport_never_follows_redirects(self):
        request=site.URLRequest('https://provider.invalid/api',data=b'{}',headers={'Authorization':'Bearer private-test-token'})
        for code in (301,302,303,307,308):
            with self.subTest(code=code), self.assertRaises(site.HTTPError):
                site.NoCredentialRedirect().redirect_request(request,None,code,'redirect',{},'https://other.invalid/collect')

    def test_dependency_response_is_bounded_and_must_be_an_object(self):
        for body,valid in [(b'{"result":[1,2,3]}',True),(b'[]',False),(b'null',False),
                           (b'{',False),(b'x'*1048577,False)]:
            response=MagicMock()
            response.__enter__.return_value=response
            response.read.return_value=body
            opener=MagicMock()
            opener.open.return_value=response
            with self.subTest(size=len(body)),patch.object(site,'build_opener',return_value=opener) as build:
                if valid:
                    self.assertEqual(site.post_json('https://service.invalid',{},'private-test-token'),{'result':[1,2,3]})
                else:
                    with self.assertRaises(ValueError):
                        site.post_json('https://service.invalid',{},'private-test-token')
                self.assertIsInstance(build.call_args.args[0],site.NoCredentialRedirect)
                response.read.assert_called_once_with(1048577)
                self.assertEqual(opener.open.call_args.kwargs['timeout'],15)

    def test_bad_dependency_json_fails_before_retrieval_without_exposing_values(self):
        with patch.object(site,'post_json',side_effect=ValueError('private-test-token')),patch.object(site,'get_index') as index:
            response=self.client.post('/api/query',json={'question':'How?'})
        self.assertEqual(response.status_code,503)
        self.assertNotIn('private-test-token',response.text)
        index.assert_not_called()

    def test_malformed_provider_shapes_are_explicit_incomplete_answers(self):
        chunks=[{'content':'facts','metadata':{'doc_title':'a.md','section_title':'Tools'},'rank':1}]
        for choices in (None,{},[],[None],[{'message':None}],[{'message':[]}],
                        [{'message':{'content':['private-test-value']}}]):
            with self.subTest(choices=choices),patch.object(site,'post_json',return_value={'choices':choices}):
                with self.assertRaises(site.HTTPException) as caught:
                    site.answer('test',chunks)
                self.assertEqual(caught.exception.status_code,502)
                self.assertNotIn('private-test-value',caught.exception.detail)


if __name__=='__main__':unittest.main()
