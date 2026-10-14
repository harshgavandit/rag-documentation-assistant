"""Behavioral regression tests; no live services or embedding download required."""
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from config import settings
from chunking.character_splitter import Chunk, split_by_characters
from chunking.semantic_splitter import split_by_headers
from generation.answer_generator import generate
from generation.prompt_builder import build_prompt
from pipeline import graph
from pipeline.rate_limit import RateLimitExceeded, enforce_rate_limit, redis_command
from retrieval.hybrid_search import search
from retrieval.semantic_search import RetrievedChunk
from vectorstore.store import get_or_create_collection, upsert_chunks


class ChunkingTests(unittest.TestCase):
    def test_invalid_overlap_cannot_hang(self) -> None:
        """A zero/negative window step must fail before looping."""
        for size, overlap in [(0, 0), (4, 4), (4, 5), (4, -1)]:
            with self.assertRaises(ValueError):
                split_by_characters('text', 'test.md', size, overlap)

    def test_code_headings_remain_inside_one_chunk(self) -> None:
        """Heading-looking code must not break fenced examples, at H2 or H3 level."""
        text = '## First\n```python\n## fake\n### fake too\n```\n### Real\nbody\n## Second\nend'
        chunks = split_by_headers(text, 'test.md', 1)
        self.assertEqual(len(chunks), 3)
        self.assertIn('## fake\n### fake too', chunks[0].content)
        self.assertEqual(chunks[0].content.count('```'), 2)
        self.assertEqual(chunks[1].metadata['subsection_title'], 'Real')
        self.assertEqual(chunks[1].metadata['section_title'], 'First')


class RateLimitTests(unittest.TestCase):
    def test_denied_and_retry_rounding(self) -> None:
        """Expose Retry-After rounded up to whole seconds."""
        with patch('pipeline.rate_limit.redis_command', return_value=[0, 0, 1501]):
            with self.assertRaises(RateLimitExceeded) as context:
                enforce_rate_limit()
        self.assertEqual(context.exception.retry_after_seconds, 2)

    def test_malformed_result_fails_closed(self) -> None:
        """An invalid Redis response must never grant admission."""
        for response in [None, [], [1, 2], [1, -1, 100], [1, 2, 0], ['1', 2, 100]]:
            with patch('pipeline.rate_limit.redis_command', return_value=response):
                with self.assertRaises(RuntimeError):
                    enforce_rate_limit()

    def test_no_request_after_rate_limit(self) -> None:
        """Rate denial happens before any retrieval, graph or paid request."""
        with patch.object(graph, 'validate_configuration'), patch.object(graph, 'build_rag_graph') as build:
            with patch.object(graph, 'enforce_rate_limit', side_effect=RateLimitExceeded(2)):
                with self.assertRaises(RateLimitExceeded):
                    graph.run_query('question')
            build.assert_not_called()

    def test_network_failure_is_safe(self) -> None:
        """Connection errors do not expose the endpoint or token."""
        with patch('pipeline.rate_limit.urlopen', side_effect=OSError('secret-token')):
            with self.assertRaises(RuntimeError) as context:
                redis_command(['PING'])
        self.assertNotIn('secret-token', str(context.exception))


class PipelineTests(unittest.TestCase):
    def test_empty_question_does_not_consume_quota(self) -> None:
        """Validate input before accessing Redis."""
        with patch.object(graph, 'enforce_rate_limit') as limiter:
            with self.assertRaises(ValueError):
                graph.run_query('   ')
            limiter.assert_not_called()

    def test_retrieval_failure_stops_graph(self) -> None:
        """LangGraph must stop before prompt or generation on retrieval failure."""
        with patch.object(graph, 'retrieve_node', return_value={'error': 'Retrieval failed'}):
            with patch.object(graph, 'build_prompt_node') as prompt, patch.object(graph, 'generate_node') as gen:
                result = graph.build_rag_graph().invoke({'query': 'q', 'error': ''})
        self.assertEqual(result['error'], 'Retrieval failed')
        prompt.assert_not_called()
        gen.assert_not_called()

    def test_prompt_failure_stops_generation(self) -> None:
        """A failed prompt must not reach the model node."""
        with patch.object(graph, 'retrieve_node', return_value={'retrieved_chunks': []}):
            with patch.object(graph, 'build_prompt_node', return_value={'error': 'Prompt failed'}):
                with patch.object(graph, 'generate_node') as gen:
                    result = graph.build_rag_graph().invoke({'query': 'q', 'error': ''})
        self.assertEqual(result['error'], 'Prompt failed')
        gen.assert_not_called()


class GenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        """Build a prompt with one allowed source label."""
        self.prompt = build_prompt('q', [RetrievedChunk('facts', {'doc_title': 'a.md', 'section_title': 'Tools'}, 1, 1)])

    def test_configured_model_and_budget_are_used(self) -> None:
        """The request must honor .env values rather than a hardcoded model."""
        response = SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(
            content='Use tools. [Source: a.md, Section: Tools]'))])
        with patch('generation.answer_generator.OpenAI') as sdk, patch.object(settings, 'validate_configuration'):
            sdk.return_value.__enter__.return_value.chat.completions.create.return_value = response
            answer = generate(self.prompt, 'q', 1)
            call = sdk.return_value.__enter__.return_value.chat.completions.create.call_args.kwargs
        self.assertEqual(call['model'], settings.OPENAI_MODEL)
        self.assertEqual(call['max_completion_tokens'], settings.OPENAI_MAX_COMPLETION_TOKENS)
        self.assertEqual(len(answer.citations), 1)

    def test_zero_context_refuses_without_paid_call(self) -> None:
        """An empty vector store must not trigger generation."""
        with patch('generation.answer_generator.OpenAI') as sdk, patch.object(settings, 'validate_configuration'):
            answer = generate(self.prompt, 'q', 0)
        sdk.assert_not_called()
        self.assertEqual(answer.text, settings.REFUSAL_TEXT)

    def test_unknown_citation_and_truncated_response_rejected(self) -> None:
        """Reject invalid source labels and incomplete answers."""
        for text, reason in [('Guess. [Source: fake.md, Section: Invented]', 'stop'),
                             ('Answer without citations', 'stop'), ('', 'stop'), ('partial', 'length')]:
            response = SimpleNamespace(choices=[SimpleNamespace(finish_reason=reason,
                                         message=SimpleNamespace(content=text))])
            with patch('generation.answer_generator.OpenAI') as sdk, patch.object(settings, 'validate_configuration'):
                sdk.return_value.__enter__.return_value.chat.completions.create.return_value = response
                with self.assertRaises(RuntimeError):
                    generate(self.prompt, 'q', 1)


class StorageTests(unittest.TestCase):
    def test_reingestion_prunes_stale_chunks_and_preserves_other_docs(self) -> None:
        """A smaller document must not leave its old trailing chunks searchable."""
        with tempfile.TemporaryDirectory(dir=settings.PROJECT_ROOT) as path:
            collection = get_or_create_collection(path, 'test_chunks')
            chunks = [Chunk('a', {'doc_title': 'a.md', 'chunk_index': i}, 1, False, False) for i in range(3)]
            chunks.append(Chunk('b', {'doc_title': 'b.md', 'chunk_index': 0}, 1, False, False))
            upsert_chunks(collection, chunks, [[1.0, 0.0]] * 4)
            upsert_chunks(collection, chunks[:1], [[1.0, 0.0]])
            self.assertEqual(set(collection.get()['ids']), {'a.md_0', 'b.md_0'})
            # Chroma owns open handles; stop the temporary client before directory cleanup.
            collection._client._system.stop()

    def test_unmatched_keywords_do_not_inflate_rrf(self) -> None:
        """Zero-score BM25 items must not gain arbitrary index-order boosts."""
        collection = MagicMock()
        collection.get.return_value = {'documents': ['apple fruit', 'pear fruit'], 'metadatas': [{}, {}], 'ids': ['a', 'b']}
        collection.query.return_value = {'ids': [['b']], 'distances': [[0.2]]}
        model = MagicMock()
        model.encode.return_value = [SimpleNamespace(tolist=lambda: [1.0, 0.0])]
        chunks = search('unmatched', collection, model, 1)
        self.assertEqual(chunks[0].content, 'pear fruit')
        self.assertAlmostEqual(chunks[0].score, 1 / 61)


if __name__ == '__main__':
    unittest.main()
