"""Review packets omit gold labels; incomplete or inconsistent reviews fail closed."""
import copy
import unittest

from evaluation.human_review import build_packet, validate_review


class HumanReviewTests(unittest.TestCase):
    def setUp(self):
        self.corpus = [{'content': 'An admitted job gets a lease.', 'metadata': {
            'doc_title': 'jobs.md', 'section_title': 'Lease', 'chunk_index': 0}}]
        self.datasets = [{'dataset_id': 'fixture', 'cases': [{
            'id': 'case-1', 'question': 'What does an admitted job get?',
            'answerable': True, 'relevant_chunk_ids': ['jobs.md::000'],
            'required_fact_patterns': ['lease'], 'tags': ['jobs']}]}]
        self.packet = build_packet(self.datasets, self.corpus,
                                   dataset_hashes={'fixture': 'a' * 64}, corpus_hash='b' * 64)
        self.review = {'packet_id': self.packet['packet_id'], 'schema_version': 1,
            'reviewer': {'public_id': 'external-reviewer', 'human_attested': True,
                         'independent_attested': True, 'used_ai': False},
            'answers': [{'case_id': self.packet['questions'][0]['case_id'], 'answerable': True,
                'relevant_chunk_ids': ['jobs.md::000'],
                'evidence': [{'chunk_id': 'jobs.md::000', 'excerpt': 'gets a lease'}]}]}

    def test_packet_omits_gold_and_does_not_claim_actual_human_review(self):
        question = self.packet['questions'][0]
        self.assertEqual(set(question), {'case_id', 'question'})
        self.assertNotIn('case-1', str(self.packet))
        result = validate_review(self.packet, self.review)
        self.assertEqual(result['annotation_count'], 1)
        self.assertFalse(result['identity_or_independence_verified'])
        self.assertEqual(result['reviewer_basis'], 'self_attestation_only')

    def test_incomplete_duplicate_unknown_and_false_excerpts_are_rejected(self):
        changes = [lambda r: r.update(answers=[]),
            lambda r: r['answers'].append(copy.deepcopy(r['answers'][0])),
            lambda r: r['answers'][0].update(relevant_chunk_ids=['unknown::000']),
            lambda r: r['answers'][0]['evidence'][0].update(excerpt='not in source'),
            lambda r: r['answers'][0].update(answerable=False),
            lambda r: r['reviewer'].update(used_ai=True),
            lambda r: r['reviewer'].update(public_id='harshgavandit'),
            lambda r: r['reviewer'].update(human_attested=False)]
        for change in changes:
            with self.subTest(change=changes.index(change)):
                review = copy.deepcopy(self.review)
                change(review)
                with self.assertRaises(ValueError):
                    validate_review(self.packet, review)

    def test_changed_packet_or_mismatched_packet_identity_is_rejected(self):
        changed = copy.deepcopy(self.packet)
        changed['questions'][0]['question'] = 'different'
        with self.assertRaises(ValueError):
            validate_review(changed, self.review)
        self.review['packet_id'] = 'c' * 64
        with self.assertRaises(ValueError):
            validate_review(self.packet, self.review)


if __name__ == '__main__':
    unittest.main()
