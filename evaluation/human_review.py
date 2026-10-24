"""Export gold-free annotation packets and validate self-attested human reviews.

No code can establish that an annotator is human or independent. This checks
complete source-supported labels and preserves that limit instead of inventing
reviewers. Public gold datasets also mean packet blinding is not guaranteed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def chunk_id(chunk):
    meta = chunk['metadata']
    return f"{meta['doc_title']}::{meta['chunk_index']:03}"


def build_packet(datasets, corpus, *, dataset_hashes, corpus_hash):
    require(bool(datasets) and bool(corpus), 'Empty review inputs')
    require(all(re.fullmatch('[a-f0-9]{64}', h) for h in [corpus_hash, *dataset_hashes.values()]),
            'Invalid source hashes')
    require({d['dataset_id'] for d in datasets} == set(dataset_hashes), 'Dataset hash bindings differ')
    chunks = {chunk_id(c): c for c in corpus}
    require(len(chunks) == len(corpus), 'Duplicate source IDs')
    questions = []
    for dataset in datasets:
        for case in dataset['cases']:
            # Omit original IDs, gold labels, tags, expected decisions and graders.
            blind_id = digest([dataset_hashes[dataset['dataset_id']], case['id']])[:16]
            questions.append({'case_id': blind_id, 'question': case['question']})
    require(len({q['case_id'] for q in questions}) == len(questions), 'Duplicate review cases')
    packet = {'schema_version': 1,
        'scope': 'Source-label annotation packet. Gold and generated outputs omitted; public repository gold remains accessible.',
        'dataset_hashes': dataset_hashes, 'corpus_sha256': corpus_hash,
        'questions': questions,
        'sources': [{'chunk_id': cid, 'document': c['metadata']['doc_title'],
                     'section': c['metadata']['section_title'], 'content': c['content']}
                    for cid, c in chunks.items()]}
    packet['packet_id'] = digest(packet)
    return packet


def validate_review(packet, review):
    require(packet.get('packet_id') == digest({k: v for k, v in packet.items() if k != 'packet_id'}),
            'Packet content or identity changed')
    require(review.get('schema_version') == 1 and review.get('packet_id') == packet['packet_id'],
            'Review packet identity mismatch')
    reviewer = review.get('reviewer', {})
    public_id = reviewer.get('public_id', '')
    require(isinstance(public_id, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{1,79}', public_id)
            and public_id.lower() != 'harshgavandit', 'External public reviewer identity is required')
    require(reviewer.get('human_attested') is True and reviewer.get('independent_attested') is True
            and reviewer.get('used_ai') is False, 'Independent human/no-AI self-attestation is required')
    sources = {s['chunk_id']: s['content'] for s in packet['sources']}
    expected = {q['case_id'] for q in packet['questions']}
    rows = review.get('answers')
    require(isinstance(rows, list) and len(rows) == len(expected), 'Review is incomplete or has duplicates')
    require({r['case_id'] for r in rows} == expected, 'Review case coverage differs')
    for row in rows:
        gold = row.get('relevant_chunk_ids')
        require(isinstance(gold, list) and len(gold) == len(set(gold)) and set(gold).issubset(sources),
                'Unknown or duplicate relevant chunks')
        require(type(row.get('answerable')) is bool and row['answerable'] == bool(gold),
                'Answerability and labels differ')
        evidence = row.get('evidence')
        require(isinstance(evidence, list) and {e['chunk_id'] for e in evidence} == set(gold),
                'Every relevant chunk needs an excerpt')
        for entry in evidence:
            require(isinstance(entry.get('excerpt'), str) and bool(entry['excerpt'].strip())
                    and entry['excerpt'] in sources[entry['chunk_id']], 'Excerpt does not match source')
        if not row['answerable']:
            require(isinstance(row.get('unsupported_reason'), str) and bool(row['unsupported_reason'].strip()),
                    'Unsupported labels need a scope reason')
    return {'schema_version': 1, 'packet_id': packet['packet_id'], 'review_sha256': digest(review),
        'annotation_count': len(rows), 'answerable_count': sum(r['answerable'] for r in rows),
        'reviewer_public_id': public_id, 'reviewer_basis': 'self_attestation_only',
        'identity_or_independence_verified': False,
        'interpretation': 'Format, completeness and source excerpts validated. This does not authenticate the reviewer, verify independence or establish answer accuracy.'}


def write_new(path, value):
    with path.open('x', encoding='utf-8', newline='\n') as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, action='append')
    parser.add_argument('--corpus', type=Path)
    parser.add_argument('--packet', type=Path)
    parser.add_argument('--review', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.packet and args.review and not args.dataset:
            value = validate_review(json.loads(args.packet.read_text(encoding='utf-8')),
                                    json.loads(args.review.read_text(encoding='utf-8')))
        elif args.dataset and args.corpus and not args.review and not args.packet:
            datasets = [json.loads(p.read_text(encoding='utf-8')) for p in args.dataset]
            hashes = {d['dataset_id']: hashlib.sha256(p.read_bytes()).hexdigest()
                      for p, d in zip(args.dataset, datasets)}
            value = build_packet(datasets, json.loads(args.corpus.read_text(encoding='utf-8')),
                                 dataset_hashes=hashes, corpus_hash=hashlib.sha256(args.corpus.read_bytes()).hexdigest())
        else:
            raise ValueError('Select dataset/corpus export or packet/review validation')
        write_new(args.output, value)
        print('Saved source-label review artifact; no human review was invented.')
        return 0
    except (ValueError, KeyError, TypeError, OSError) as error:
        print('Review validation failed: ' + str(error))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
