# Frozen multi-domain retrieval diagnostic

This separate diagnostic checks the existing Docwise retrieval implementation on six public repository documents from five application domains. It contains **60 authored questions: 50 answerable and 10 unsupported**, with exact source excerpts and stable chunk IDs. It does not change the two educational documents or 39 chunks used by the hosted application. The original four retrieval/answer reports and their source snapshots remain historical evidence.

The source manifest pins each document to a full Git commit, immutable GitHub URL and SHA-256 of its original bytes. It also records the original path, domain and actual repository license status. Only the community repository has a license file at its captured revision; no license is invented for the other snapshots. These are documents from repositories owned by the same original author, not an external benchmark or independently human-labeled dataset. Statements describe the captured documents, not independent verification of those applications. The durable-jobs material describes an implementation lab; its cloud architecture is documented as undeployed.

| Query partition | Domains | Documents | Questions | Answerable | Unsupported |
| --- | --- | ---: | ---: | ---: | ---: |
| Development | Banking action governance, semantic caching, realtime coordination | 3 | 36 | 30 | 6 |
| Heldout diagnostic | Community approvals/privacy, durable jobs/effects | 3 | 24 | 20 | 4 |

The partitions separate query domains. Every source document is available to retrieval in both partitions. The heldout diagnostic is public, authored and inspectable; it is not a hidden external research test, production traffic or generated-answer evaluation. Do not use its results to choose ranking parameters and then describe the same scores as untouched heldout evidence. Each domain has ten distinct answerable intents and two unsupported questions. Cases inspect failure behavior, configuration, scope limits and multi-part guarantees as well as exact terms.

## Authoring, independent source review and freeze

An AI agent authored the cases from the six frozen snapshots before running any broader retrieval rankings. A separate AI agent reviewed all 60 cases directly against the source text, confirmed their answerability and scope, and identified five additional partially supporting chunks under the existing binary relevance policy. Annotation revision 2 includes those additions. The [review record](source_review.json) lists the exact cases, chunk IDs, excerpts and reasons. This is independent AI source review; no human labels or human agreement score are claimed.

The initial revision-1 [freeze](annotation_history/revision1-freeze.json), [development cases](annotation_history/revision1-dev.json) and [heldout cases](annotation_history/revision1-heldout.json) retain the original bytes. The current [freeze manifest](freeze.json) binds the reviewed datasets, source snapshots, corpus, source manifest and review history. Validation permits precisely the five recorded pre-ranking label additions between revisions. The questions, intents, answerability labels and partition assignments did not change. A later annotation change requires a new revision and an explicit record, preserving earlier measurements.

Every known chunk directly supporting any requested proposition is relevant, including alternative summaries and partial support for a multi-part question. Exact supporting excerpts establish text presence, not semantic correctness or annotation completeness. Heading-only mentions are excluded. Unsupported cases have source-scope explanations instead of fabricated gold evidence. Chunk recall counts labeled chunks rather than complete answer components; multiple alternative chunks can support the same proposition.

## Corpus and encoder limits

The corpus contains **32 heading-based chunks**. Its deterministic export reads retained source snapshots in manifest order and calls the existing `split_by_headers(text, doc_title, max_words=400)` implementation. Corpus IDs are `<doc_title>::<document-local chunk_index padded to three digits>`. Domain, pinned source URL, revision and source hash accompany every chunk. The export is canonical UTF-8 JSON with LF, sorted keys, two-space indentation and a final newline. `--check-build` rebuilds in memory and rejects byte drift; it never rewrites frozen inputs.

The production encoder truncates at **256 tokens**. An audit with the pinned tokenizer, without running the ONNX model, found **13 of 32 chunks exceed that limit; the longest has 785 tokens before truncation**. Counts for every chunk and the tokenizer hash are retained in the source manifest and datasets. The current chunker splits long H2 sections at H3 headings; it does not enforce a hard token cap when there are no H3 headings. That behavior is preserved. BM25 sees full chunk text, while the vector encoder may omit later evidence in a chunk. This can affect method comparisons and is a measured input limitation, not a reason to discard difficult cases.

## Reproduce without provider calls

Use Python 3.12 and the pinned environment in `evaluation/requirements.lock.txt`. Prepare the checksum-verified public artifacts with `python web/prepare.py` if they are not already available. Model preparation may download public artifacts. Validation, source review checks and report verification need no credentials or provider access; retrieval measurement runs only the prepared local ONNX encoder.

From the repository root:

```powershell
python -m evaluation.broader --check-build --validate-only
python -m evaluation.broader --validate-only --check-encoder
python -m evaluation.run --dataset evaluation/broader_v1/dev.json --corpus evaluation/broader_v1/corpus.json --validate-only
python -m evaluation.run --dataset evaluation/broader_v1/heldout.json --corpus evaluation/broader_v1/corpus.json --validate-only
python -m unittest discover -s web/tests -v
python -m evaluation.verify_evidence
```

Use the broader runner for measurements so reports retain the complete source bundle as well as the standard evaluator snapshots:

```powershell
python -m evaluation.broader --run-split dev --output evaluation/results/broader-v1-dev-new.json --repeats 3 --warmup-rounds 1
python -m evaluation.broader --run-split heldout --output evaluation/results/broader-v1-heldout-new.json --repeats 3 --warmup-rounds 1
python -m evaluation.broader --verify-report evaluation/results/broader-v1-dev-new.json
python -m evaluation.broader --verify-report evaluation/results/broader-v1-heldout-new.json
```

The output parent must exist. Each filename and its snapshot prefix must be new; existing reports are rejected before local model work. Measurement is gated on the completed independent source review. An interrupted run leaves an explicit incomplete marker. The runner uses the same tokenization, BM25 scoring, vector encoding and reciprocal rank fusion as production, passing the alternate frozen chunks explicitly to `Index`. Default `Index()` and evaluation without `--corpus` continue using the deployed corpus.

Reports retain raw rankings, missed gold IDs, measured timing samples, per-method metrics, overlapping tag breakdowns, runtime/package/model identity and exact source fingerprints. The metric and timing protocol is the same as the [existing retrieval evaluation](../README.md): answerable-query macro means for hit@1, hit@5, MRR@5, recall@5 and nDCG@5; unsupported rankings have null ranking metrics. Every complete miss and partial omission stays in the raw records. There is no retrieval abstention threshold, so nonempty rankings for unsupported questions are diagnostic, not answerability success.

The broader verifier checks all retained source bytes, immutable source bindings, annotation history, partition coverage, corpus reconstruction, model identity, intent IDs, raw ranking metrics, timing aggregates and tag denominators. It runs without models, credentials or network access. Hashes detect inconsistent evidence and drift; they are not signatures, provider-call attestation or independent proof that a timing measurement occurred. The existing historical verifier continues checking exactly its original four reports.

Do not compare these scores directly with the earlier two-document datasets as an improvement claim: the corpus and query distributions differ. Record the development and heldout diagnostic results separately, retain failures and state that neither partition measures sentence-level answer entailment or human judgment.
