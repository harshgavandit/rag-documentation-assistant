"""Validate, reproduce and verify the separate frozen multi-domain diagnostic.

Validation and report verification use no models, credentials or network. The
optional encoder audit tokenizes local snapshots without running an ONNX model.
Measurement uses only the prepared local retrieval encoder, never a provider.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import quote

from chunking.semantic_splitter import split_by_headers
from evaluation.run import (ROOT, chunk_id, evaluate, load_dataset, retain_sources)
from evaluation.verify_evidence import (compare, read_json, require, snapshot,
                                        validate_dataset, verify_retrieval)

BASE = ROOT / 'evaluation/broader_v1'
PARTITIONS = {'dev': ['banking', 'cache', 'realtime'],
              'heldout': ['community', 'durable_jobs']}
REVIEWED = 'completed_independent_agent_review'


def digest(content):
    return hashlib.sha256(content).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True,
                       allow_nan=False) + '\n').encode('utf-8')


def safe_path(base, relative):
    require(isinstance(relative, str) and not Path(relative).is_absolute(), 'Invalid bundle path')
    path = (base / relative).resolve()
    require(path.is_relative_to(base.resolve()), 'Bundle path outside frozen directory')
    return path


def bundle_bytes(base=BASE):
    """Read only the explicitly bound snapshot inventory, never env files."""
    freeze = read_json(base / 'freeze.json')
    return {name: safe_path(base, name).read_bytes() for name in freeze['files']}


def build_corpus(manifest, files):
    """Reproduce corpus bytes from retained documents with production chunking."""
    chunks = []
    for source in manifest['source_documents']:
        text = files[source['snapshot_path']].decode('utf-8')
        for chunk in split_by_headers(text, source['doc_title'], 400):
            metadata = dict(chunk.metadata, domain=source['domain'],
                            source_url=source['source_url'], source_revision=source['revision'],
                            source_sha256=source['sha256'])
            chunks.append({'content': chunk.content, 'metadata': metadata})
    return chunks


def validate_contents(files, freeze, require_review=False):
    """Check hashes, source attribution, deterministic export, labels and splits."""
    require(freeze['schema_version'] == 1, 'Unsupported freeze schema')
    require(freeze['benchmark_id'] == 'docwise-broader-diagnostic-v1', 'Wrong benchmark identity')
    require(set(files) == set(freeze['files']), 'Incomplete frozen file inventory')
    for name, expected in freeze['files'].items():
        require(digest(files[name]) == expected, 'Frozen file hash mismatch: ' + name)
    manifest = json.loads(files['manifest.json'])
    require(manifest['schema_version'] == 1 and manifest['benchmark_id'] == freeze['benchmark_id'], 'Manifest identity mismatch')
    compare(manifest['split_domains'], PARTITIONS, 'Domain partition contract')
    require(manifest['chunking']['implementation'] == 'chunking.semantic_splitter.split_by_headers'
            and manifest['chunking']['max_words'] == 400, 'Chunking contract mismatch')
    sources = manifest['source_documents']
    require(len(sources) == 6, 'Expected six frozen source documents')
    titles, paths, domains = set(), set(), set()
    for source in sources:
        require(source['doc_title'] not in titles and source['snapshot_path'] not in paths, 'Duplicate source identity')
        titles.add(source['doc_title'])
        paths.add(source['snapshot_path'])
        domains.add(source['domain'])
        require(re.fullmatch(r'[0-9a-f]{40}', source['revision']) is not None, 'Source revision must be immutable')
        require(source['repository'].startswith('harshgavandit/'), 'Unexpected source repository owner')
        expected_url = f"https://github.com/{source['repository']}/blob/{source['revision']}/{quote(source['original_path'], safe='/')}"
        require(source['source_url'] == expected_url, 'Source URL is not the immutable revision URL')
        require(source['snapshot_path'] == 'sources/' + source['doc_title'], 'Source path and document title disagree')
        require(digest(files[source['snapshot_path']]) == source['sha256'], 'Source snapshot hash mismatch')
        require(source['license']['status'] in ('MIT', 'no_repository_license_file_at_captured_revision'), 'Missing honest license status')
    require(domains == {domain for values in PARTITIONS.values() for domain in values}, 'Source domain coverage mismatch')
    history_names = {'source_review.json', 'annotation_history/revision1-freeze.json',
                     'annotation_history/revision1-dev.json', 'annotation_history/revision1-heldout.json'}
    expected_names = {'manifest.json', 'corpus.json', 'dev.json', 'heldout.json'} | paths | history_names
    require(set(files) == expected_names, 'Unexpected or missing frozen source files')
    corpus = json.loads(files['corpus.json'])
    require(json_bytes(build_corpus(manifest, files)) == files['corpus.json'], 'Deterministic corpus export mismatch')
    diagnostics = manifest['encoder_input_diagnostics']
    counts = diagnostics['per_chunk_untruncated_tokens']
    require(diagnostics['encoder_token_limit'] == 256, 'Encoder token limit mismatch')
    require([row['chunk_id'] for row in counts] == [chunk_id(chunk) for chunk in corpus], 'Encoder audit chunk coverage mismatch')
    require(all(type(row['token_count']) is int and row['token_count'] > 0 for row in counts), 'Invalid token count')
    require(diagnostics['chunk_count'] == len(corpus), 'Encoder audit denominator mismatch')
    require(diagnostics['truncated_chunk_count'] == sum(row['token_count'] > 256 for row in counts), 'Encoder truncation count mismatch')
    require(diagnostics['maximum_untruncated_tokens'] == max(row['token_count'] for row in counts), 'Encoder maximum mismatch')
    case_ids, intents, questions, partitions, all_cases = set(), set(), set(), {}, []
    for partition, expected_domains in PARTITIONS.items():
        dataset = json.loads(files[partition + '.json'])
        require(dataset['schema_version'] == 1, 'Unsupported dataset schema')
        require(dataset['partition'] == partition and dataset['domains'] == expected_domains, 'Dataset domain partition mismatch')
        require(dataset['dataset_id'] == 'docwise-broader-v1-' + partition, 'Dataset identity mismatch')
        require(dataset['annotation_revision'] == freeze['annotation_revision'], 'Annotation revision mismatch')
        require(dataset['corpus']['sha256'] == digest(files['corpus.json']), 'Dataset corpus hash mismatch')
        compare(dataset['corpus']['encoder_input_diagnostics'], diagnostics, 'Dataset encoder audit')
        validate_dataset(dataset, corpus)
        for case in dataset['cases']:
            require(re.fullmatch(r'docwise-v3-\d{3}', case['id']) is not None and case['id'] not in case_ids, 'Invalid or duplicate case ID')
            case_ids.add(case['id'])
            require(bool(case['intent_id']) and case['intent_id'] not in intents, 'Duplicate or missing intent across partitions')
            intents.add(case['intent_id'])
            question = ' '.join(case['question'].casefold().split())
            require(bool(question) and question not in questions, 'Duplicate or missing question across partitions')
            questions.add(question)
            require(case['domain'] in expected_domains and case['domain'] in case['tags'], 'Case domain outside assigned partition')
            for evidence in case['evidence']:
                chunk = next(chunk for chunk in corpus if chunk_id(chunk) == evidence['chunk_id'])
                require(chunk['metadata']['domain'] == case['domain'], 'Gold evidence domain mismatch')
            require(case['answerable'] or bool(case.get('unsupported_reason', '').strip()), 'Unsupported case needs a source-scope reason')
        for domain in expected_domains:
            group = [case for case in dataset['cases'] if case['domain'] == domain]
            require(len(group) == 12 and sum(case['answerable'] for case in group) == 10, 'Domain case inventory mismatch')
        partitions[partition] = len(dataset['cases'])
        all_cases.extend(dataset['cases'])
    require(len(all_cases) == freeze['case_count'] == 60, 'Frozen case count mismatch')
    require(sum(case['answerable'] for case in all_cases) == freeze['answerable_case_count'] == 50, 'Frozen answerable count mismatch')
    require(partitions == {'dev': freeze['dev_case_count'], 'heldout': freeze['heldout_case_count']} == {'dev': 36, 'heldout': 24}, 'Frozen split denominator mismatch')
    require(freeze['retrieval_tuning_performed'] is False and freeze['provider_requests_made'] == 0, 'Frozen premeasurement protocol mismatch')
    review = json.loads(files['source_review.json'])
    previous = json.loads(files['annotation_history/revision1-freeze.json'])
    require(review['annotation_revision'] == freeze['annotation_revision'] == 2, 'Review annotation revision mismatch')
    require(review['reviewer_kind'] == 'independent_AI_agent' and review['human_labeled'] is False
            and review['independently_human_reviewed'] is False, 'Source review must be identified as AI review')
    require(review['reviewed_case_count'] == 60 and len(review['corrections']) == 5, 'Source review coverage mismatch')
    prior_hash = digest(files['annotation_history/revision1-freeze.json'])
    require(review['previous_freeze']['sha256'] == freeze['source_review']['previous_freeze_sha256'] == prior_hash,
            'Previous freeze hash mismatch')
    current_cases = {case['id']: case for case in all_cases}
    prior_cases = {}
    for partition in PARTITIONS:
        old_bytes = files[f'annotation_history/revision1-{partition}.json']
        require(digest(old_bytes) == previous['files'][partition + '.json'], 'Prior annotation dataset hash mismatch')
        prior_cases.update({case['id']: case for case in json.loads(old_bytes)['cases']})
    require(set(prior_cases) == set(current_cases), 'Review changed the frozen intent inventory')
    for correction in review['corrections']:
        case = prior_cases[correction['case_id']]
        require(correction['added_chunk_id'] not in case['relevant_chunk_ids'], 'Review addition duplicates an old label')
        case['relevant_chunk_ids'].append(correction['added_chunk_id'])
        case['evidence'].append({'chunk_id': correction['added_chunk_id'], 'excerpt': correction['excerpt']})
    compare(current_cases, prior_cases, 'Only recorded pre-ranking source-label additions are allowed')
    if require_review:
        require(freeze['source_review']['status'] == REVIEWED, 'Independent source review must finish before retrieval measurement')
    return {'valid': True, 'case_count': len(all_cases), 'answerable_case_count': 50,
            'partitions': partitions, 'source_document_count': len(sources), 'domain_count': len(domains),
            'chunk_count': len(corpus), 'truncated_chunk_count': diagnostics['truncated_chunk_count'],
            'source_review_status': freeze['source_review']['status'], 'provider_requests_made': 0}


def validate_bundle(base=BASE, require_review=False, check_encoder=False):
    files = bundle_bytes(base)
    freeze = read_json(base / 'freeze.json')
    result = validate_contents(files, freeze, require_review)
    if check_encoder:
        from tokenizers import Tokenizer
        tokenizer_path = ROOT / 'web/models/tokenizer.json'
        manifest = json.loads(files['manifest.json'])
        diagnostics = manifest['encoder_input_diagnostics']
        require(digest(tokenizer_path.read_bytes()) == diagnostics['tokenizer_sha256'], 'Encoder tokenizer hash mismatch')
        tokenizer = Tokenizer.from_file(str(tokenizer_path))
        tokenizer.no_truncation()
        tokenizer.no_padding()
        computed = [{'chunk_id': chunk_id(chunk), 'token_count': len(tokenizer.encode(chunk['content']).ids)}
                    for chunk in json.loads(files['corpus.json'])]
        compare(diagnostics['per_chunk_untruncated_tokens'], computed, 'Untruncated local tokenizer audit')
        result['encoder_token_counts_recomputed'] = True
    return result


def retain_bundle(report, output_path, base=BASE):
    """Retain every source snapshot needed to audit an alternate-corpus report."""
    files = bundle_bytes(base)
    files['freeze.json'] = (base / 'freeze.json').read_bytes()
    files['broader.py'] = Path(__file__).read_bytes()
    files['semantic_splitter.py'] = (ROOT / 'chunking/semantic_splitter.py').read_bytes()
    files['character_splitter.py'] = (ROOT / 'chunking/character_splitter.py').read_bytes()
    snapshots = {}
    folder = output_path.parent / 'sources'
    folder.mkdir(parents=True, exist_ok=True)
    for ordinal, (name, content) in enumerate(sorted(files.items())):
        target = folder / f'{output_path.stem}-broader-{ordinal:02d}.txt'
        require(not target.exists(), 'Broader snapshot already exists; choose a new report filename')
        target.write_bytes(content)
        snapshots[name] = {'path_relative_to_report': target.relative_to(output_path.parent).as_posix(),
                           'sha256': digest(content)}
    report['broader_evidence'] = {'benchmark_id': 'docwise-broader-diagnostic-v1',
                                 'exact_byte_snapshots': snapshots, 'provider_requests_made': 0}


def verify_report(path):
    """Recompute evidence from retained bytes, without a model or provider call."""
    report = read_json(path)
    require(report['retrieval_scope'] == 'separate_frozen_diagnostic_corpus', 'Report is not a separate-corpus diagnostic')
    broad = report['broader_evidence']
    require(broad['benchmark_id'] == 'docwise-broader-diagnostic-v1' and broad['provider_requests_made'] == 0, 'Wrong broader evidence scope')
    saved = {name: snapshot(path.parent, entry['path_relative_to_report'], entry['sha256']).read_bytes()
             for name, entry in broad['exact_byte_snapshots'].items()}
    freeze = json.loads(saved['freeze.json'])
    expected = set(freeze['files']) | {'freeze.json', 'broader.py', 'semantic_splitter.py', 'character_splitter.py'}
    require(set(saved) == expected, 'Incomplete broader report snapshot inventory')
    result = validate_contents({name: saved[name] for name in freeze['files']}, freeze, require_review=True)
    partition = next((name for name in PARTITIONS if report['dataset']['id'] == 'docwise-broader-v1-' + name), None)
    require(partition is not None, 'Unknown broader report partition')
    dataset = json.loads(saved[partition + '.json'])
    bindings = {'dataset': digest(saved[partition + '.json']), 'corpus': digest(saved['corpus.json']),
                'engine': report['source']['engine_sha256'], 'evaluator': report['source']['evaluator_sha256'],
                'metrics': report['source']['metrics_sha256'], 'model_manifest': report['model']['manifest_sha256']}
    require(set(report['source']['exact_byte_snapshots']) == set(bindings), 'Incomplete retrieval source manifest')
    retained = {}
    for name, expected_hash in bindings.items():
        entry = report['source']['exact_byte_snapshots'][name]
        require(entry['sha256'] == expected_hash, 'Retrieval source binding hash mismatch')
        retained[name] = snapshot(path.parent, entry['path_relative_to_report'], expected_hash).read_bytes()
    require(retained['dataset'] == saved[partition + '.json'] and retained['corpus'] == saved['corpus.json'], 'Selected retrieval input and bundle disagree')
    require(report['dataset']['sha256'] == bindings['dataset'], 'Dataset report hash mismatch')
    compare(report['dataset'], {'id': dataset['dataset_id'], 'label': dataset['label'],
            'sha256': bindings['dataset'], 'annotation_revision': dataset['annotation_revision'],
            'case_count': len(dataset['cases']),
            'answerable_case_count': sum(case['answerable'] for case in dataset['cases'])}, 'Report dataset identity')
    compare(report['corpus'], dataset['corpus'], 'Report corpus identity')
    require(report['protocol']['top_k'] == 5 and type(report['protocol']['warm_repeats_per_case']) is int
            and report['protocol']['warm_repeats_per_case'] >= 1, 'Invalid retrieval protocol')
    model_manifest = json.loads(retained['model_manifest'])
    require(report['model']['model'] == model_manifest['model'] and report['model']['revision'] == model_manifest['revision'], 'Model identity mismatch')
    compare(report['model']['artifact_sha256'], {name: info['sha256'] for name, info in model_manifest['files'].items()}, 'Model artifact binding')
    require(model_manifest['files']['tokenizer.json']['sha256'] == dataset['corpus']['encoder_input_diagnostics']['tokenizer_sha256'], 'Encoder diagnostic tokenizer binding mismatch')
    require('by_tag' in report and set(report['summary']) == {'bm25', 'vector', 'hybrid'}, 'Missing method or tag breakdown')
    require([row['intent_id'] for row in report['cases']] == [case['intent_id'] for case in dataset['cases']], 'Case intent identity mismatch')
    verify_retrieval(report, dataset, json.loads(saved['corpus.json']))
    return dict(result, verified=True, verified_partition=partition, retrieval_cases_verified=len(dataset['cases']))


def run_split(partition, output, base=BASE, repeats=3, warmup_rounds=1):
    """Run unchanged ranking on a reviewed bundle, preserving every raw result."""
    validate_bundle(base, require_review=True)
    require(output.suffix == '.json' and output.parent.is_dir(), 'Output must be a JSON file in an existing directory')
    require(not output.exists(), 'Report already exists; choose a new output filename')
    require(not any((output.parent / 'sources').glob(output.stem + '-*')), 'Snapshots already exist; choose a new output filename')
    dataset_path, corpus_path = base / (partition + '.json'), base / 'corpus.json'
    dataset, corpus = load_dataset(dataset_path, corpus_path)
    # Exclusive creation precedes local model work. An interrupted run leaves a
    # visible incomplete marker instead of overwriting or implying success.
    with output.open('x', encoding='utf-8') as stream:
        stream.write('{"status":"incomplete_local_retrieval_measurement"}\n')
    report = evaluate(dataset, corpus, repeats, warmup_rounds, dataset_path, corpus_path)
    retain_sources(report, dataset_path, output, corpus_path)
    retain_bundle(report, output, base)
    output.write_bytes(json_bytes(report))
    verify_report(output)
    return {'completed': True, 'partition': partition, 'summary': report['summary'], 'provider_requests_made': 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, default=BASE)
    parser.add_argument('--check-build', action='store_true', help='rebuild corpus in memory and compare; validation always includes this')
    parser.add_argument('--validate-only', action='store_true')
    parser.add_argument('--check-encoder', action='store_true', help='recompute token counts using the prepared local tokenizer only')
    parser.add_argument('--verify-report', type=Path)
    parser.add_argument('--run-split', choices=tuple(PARTITIONS))
    parser.add_argument('--output', type=Path)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--warmup-rounds', type=int, default=1)
    args = parser.parse_args(argv)
    if args.repeats < 1 or args.warmup_rounds < 1:
        parser.error('repeats and warmup-rounds must be at least 1')
    if args.run_split and (args.output is None or args.validate_only or args.verify_report):
        parser.error('--run-split requires --output and cannot be combined with validation or report verification')
    try:
        if args.verify_report:
            result = verify_report(args.verify_report)
        elif args.run_split:
            result = run_split(args.run_split, args.output, args.bundle, args.repeats, args.warmup_rounds)
        else:
            result = validate_bundle(args.bundle, check_encoder=args.check_encoder)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0
    except (ValueError, KeyError, TypeError, OSError, StopIteration) as error:
        print('Broader diagnostic validation failed: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
