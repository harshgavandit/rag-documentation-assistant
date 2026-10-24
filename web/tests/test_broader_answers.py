"""Returned usage estimates do not turn missing data into zero-cost evidence."""
import unittest

from evaluation.broader_answers import checks, summarize


class BroaderAnswerTests(unittest.TestCase):
    def test_returned_usage_pricing_and_missing_usage_are_separate(self):
        row = {'status': 'completed', 'usage': {'prompt_tokens': 1000, 'completion_tokens': 100,
                'total_tokens': 1100, 'cached_prompt_tokens': 500},
               'checks': {'expected_refusal_matches': True, 'non_refusal_labels_valid': True,
                          'refused': False, 'expected_answerable': True}}
        summary = summarize([row, {'status': 'error'}])
        self.assertEqual(summary['records_with_complete_usage'], 1)
        self.assertEqual(summary['records_priced_from_returned_usage'], 1)
        self.assertAlmostEqual(summary['returned_usage_estimate_usd'], .000105)
        self.assertEqual(summary['human_evaluated_answers'], 0)
        self.assertEqual(summarize([{'status': 'error'}])['returned_usage_estimate_usd'], None)

    def test_label_and_expected_refusal_proxies_do_not_claim_semantic_correctness(self):
        chunks = [{'metadata': {'doc_title': 'd', 'section_title': 's', 'chunk_index': 0}}]
        result = checks({'answerable': True, 'relevant_chunk_ids': ['d::000']},
                        {'refused': False, 'citations': ['[Source: d, Section: s]']}, chunks)
        self.assertTrue(result['expected_refusal_matches'])
        self.assertTrue(result['non_refusal_labels_valid'])
        self.assertFalse(result['semantic_correctness_human_reviewed'])


if __name__ == '__main__':
    unittest.main()
