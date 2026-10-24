# Docwise authored retrieval diagnostic v2

This separately authored expansion contains **112 distinct question intents**: 48 Maps questions, 48 healthcare questions and 16 unsupported requests. It targets code-field details, boundaries, operational instructions, explicit ambiguity and comparisons across sections of the same two bundled guides. It is a synthetic repository diagnostic. It is **not an external benchmark, a held-out sample, independently human-labeled data, production traffic or generated-answer evaluation**. The author had access to v1 context; related subject matter necessarily overlaps because both sets use the same small corpus. More authored cases do not establish generalization to other documents.

The existing [v1 dataset](cases.json), [v1 methodology](README.md) and original retained results are preserved. Running without `--dataset` still selects v1. Comparing v1 and v2 scores as a model improvement would be invalid: they contain different query distributions and gold annotations. Production retrieval was unchanged.

## Authoring, review and freeze

Each new case has a stable `docwise-v2-NNN` ID, a unique `intent_id`, a question, tags, binary answerability, gold chunk IDs and exact supporting excerpts. Unsupported questions carry a source-scope reason instead of a fabricated gold chunk. Intent IDs document the specific information being requested; they are not a claim of independent statistical observations. Some related intents deliberately inspect different fields or decision boundaries within one implementation.

Initial questions and labels were frozen before any v2 retrieval ranking was inspected. A separate AI agent then reviewed all 112 question intents against the 39-chunk corpus, with no ranking outputs or provider answers. That source audit added omitted direct/partial evidence in cases 008, 010, 023, 039, 045, 047 and 054; removed premise-only evidence in 084; and clarified content-block terminology in 029. Those changes and the initial freeze hash are recorded in `annotation_review`. This is AI-agent review, not a human annotation study or inter-annotator agreement measurement. The final revision-2 freeze preceded the first v2 retrieval run.

The final dataset SHA-256 is `be2aefd46202228095fbe420d4833013f2f82d11f1d978363d17099275091152`. The frozen corpus SHA-256 is `9a8242dcc4309057921dcc635783cf8415b4012155704878ad58ad15329620a7`. Every excerpt was checked as an exact substring of its identified chunk. Source presence alone does not prove that a relevance judgment is correct or complete; alternative valid chunks can remain omitted.

Gold chunk IDs use `<metadata.doc_title>::<document-local chunk_index padded to three digits>`. A chunk must directly support an explicitly requested proposition, including a relevant part of a multi-part question. Relevant examples and summaries count; heading-only mentions and text that merely repeats a premise do not. Binary relevance does not distinguish indispensable evidence from alternative evidence. Recall therefore means the fraction of labeled relevant chunks retrieved, not the fraction of answer components covered.

Tags are authored before ranking. `ambiguous` means the question explicitly resolves a naming discrepancy, boundary or misleading assumption; it does not mean that several incompatible answers should be accepted without context. `multi_section` marks six queries relating material across sections, including naming and role comparisons. A summary or a supplied question premise can let a single chunk answer one of these queries, so this tag does not certify multi-hop reasoning or a need to retrieve several chunks. These tags overlap with guide and style tags and must not be added as mutually exclusive groups.

## Reproduce without provider requests

Use the existing Python 3.12 environment and public pinned embedding artifacts. Preparation can download the public files; the evaluator itself has no network or paid-provider calls.

```powershell
python -m pip install -r web/requirements.txt
python web/prepare.py
python -m evaluation.run --dataset evaluation/cases_v2.json --validate-only
python -m unittest discover -s web/tests -p "test_evaluation*.py" -v
python -m evaluation.run --dataset evaluation/cases_v2.json --output evaluation/results/retrieval-v2-new.json --repeats 3 --warmup-rounds 1
```

The CLI rejects stale corpus bytes, unknown or duplicate gold IDs, unmatched excerpts, conflicting answerability, duplicate question text, invalid returned IDs and rankings that change across measured repeats. It does not enforce a quality threshold chosen to fit these results. A command failure is an execution or data-validity failure; a low retrieval score is retained evidence.

The default dataset and existing function signatures remain compatible. An explicitly selected dataset controls the recorded dataset hash. The report includes `by_tag` macro summaries, complete-miss IDs for each method and `missed_relevant_chunk_ids` for every query, alongside raw rankings and raw warm timing samples.

## Measured protocol

BM25 and vector baselines reuse the actual production index, tokenizer, model and corpus. BM25 uses production positive-score filtering and stable lexical ties. Vector uses production ONNX encoding, cosine scores and NumPy sorting. Hybrid calls the unchanged `web.engine.Index.search(question, top_k=5)`, retaining its Reciprocal Rank Fusion constant of 60 and at most ten candidates per leg. No method was tuned or selected as a new production default from this set.

Ranking metrics are binary hit@1, hit@5, MRR@5, recall@5 and nDCG@5. Scores are macro means over the 96 answerable queries. nDCG uses unit gains, `1/log2(rank+1)` discounts and the best possible ordering of up to five relevant chunks. Unsupported queries receive null ranking metrics; all retrieval methods can still return unrelated chunks. No retrieval abstention threshold, refusal correctness, answer entailment or factual correctness of the educational guides is evaluated.

One fresh evaluator process constructs one index. Dependency imports, construction and first method calls are separate startup fields. All 112 queries receive one excluded warmup round. Each then receives three measured repeats in fixed BM25/vector/hybrid order: 336 retained warm latency samples per method. Percentiles interpolate linearly at `(n-1)*p`. OS/filesystem caches are not cleared; later methods share an index and can benefit from prior calls. Warm calls include local retrieval computation and exclude downloads, HTTP, admission, network and generation. This is not a controlled hardware comparison, cold-machine benchmark, deployed latency measurement or SLA.

The retained run used Python 3.12.14 on Windows 11, CPUExecutionProvider, two intra-op threads and one inter-op thread, model revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`. Exact package versions and artifact hashes appear in the report. It records base Git SHA `909f48e40d57886ccc427fb3d8c3c47f3b2088f5` and a dirty working tree because the evaluation expansion was uncommitted.

## Recorded results

[Raw v2 report](results/retrieval-v2-2026-10-24.json). Values below are generated from that exact retained report. All 96 answerable queries contribute equally.

| Method | Hit@1 | Hit@5 | MRR@5 | Recall@5 | nDCG@5 |
| --- | ---: | ---: | ---: | ---: | ---: |
| BM25 | 0.7917 | 0.9792 | 0.8661 | 0.9229 | 0.8568 |
| Vector | 0.6042 | 0.8958 | 0.7115 | 0.8332 | 0.7150 |
| Production hybrid | 0.7812 | 0.9688 | 0.8514 | 0.9156 | 0.8443 |

BM25 leads hybrid on the overall authored set. Hybrid does better on some overlapping subsets, including the Maps, explicit-ambiguity and multi-section groups below. These observations do not justify changing production ranking without broader evidence.

| Group | Answerable cases | Method | Hit@5 | MRR@5 | Recall@5 | nDCG@5 |
| --- | ---: | --- | ---: | ---: | ---: | ---: |
| maps | 48 | bm25 | 0.9792 | 0.8462 | 0.8854 | 0.8198 |
| maps | 48 | vector | 0.9167 | 0.7156 | 0.8142 | 0.7012 |
| maps | 48 | hybrid | 0.9792 | 0.8757 | 0.9062 | 0.8522 |
| healthcare | 48 | bm25 | 0.9792 | 0.8861 | 0.9604 | 0.8938 |
| healthcare | 48 | vector | 0.8750 | 0.7073 | 0.8521 | 0.7288 |
| healthcare | 48 | hybrid | 0.9583 | 0.8271 | 0.9250 | 0.8364 |
| ambiguous | 10 | bm25 | 1.0000 | 0.8083 | 0.8500 | 0.7499 |
| ambiguous | 10 | vector | 1.0000 | 0.8833 | 0.9000 | 0.8350 |
| ambiguous | 10 | hybrid | 1.0000 | 0.9500 | 0.9000 | 0.8777 |
| multi_section | 6 | bm25 | 0.8333 | 0.7222 | 0.5167 | 0.5104 |
| multi_section | 6 | vector | 0.8333 | 0.6389 | 0.6500 | 0.5733 |
| multi_section | 6 | hybrid | 1.0000 | 0.8056 | 0.7333 | 0.6541 |

All **16 unsupported questions receive nonempty rankings under every method**. Their metrics are null, and the report breaks them down by authored reason. Returning chunks does not demonstrate that a question is answerable. The generation layer must decide how to respond; it was not invoked in this experiment.

### Every complete retrieval miss

A complete miss means no labeled relevant chunk was present in the first five results. Partial evidence omissions remain visible separately in each case record. The table includes the union of all methods' complete misses; none were removed from aggregation.

| Case | Intent | Methods with complete miss |
| --- | --- | --- |
| docwise-v2-007 | maps_dotenv_file | vector |
| docwise-v2-009 | maps_agent_files | bm25 |
| docwise-v2-024 | maps_mcp_name_tools | vector |
| docwise-v2-047 | maps_interface_vs_mapping | vector |
| docwise-v2-048 | maps_instruction_discovery | vector, hybrid |
| docwise-v2-049 | health_primitives | vector |
| docwise-v2-057 | health_demographic_columns | bm25, hybrid |
| docwise-v2-062 | health_lab_columns | vector |
| docwise-v2-064 | health_sqlite_choice | hybrid |
| docwise-v2-088 | health_fastmcp_protocol_roles | vector |
| docwise-v2-091 | health_seed_groups | vector |
| docwise-v2-092 | health_thin_server | vector |
| docwise-v2-096 | health_cloud_front | vector |

The hybrid misses concern the Maps instruction/discovery relation (048), healthcare demographic columns (057) and SQLite rationale (064). The report retains actual retrieved chunk IDs for inspection. The gold labels and production retriever were left unchanged after these rankings were measured.

### Local timing

| Method | Samples | Warm p50 | Warm p95 | Warm p99 |
| --- | ---: | ---: | ---: | ---: |
| bm25 | 336 | 0.152 ms | 0.410 ms | 0.683 ms |
| vector | 336 | 2.750 ms | 5.155 ms | 5.921 ms |
| hybrid | 336 | 3.012 ms | 5.795 ms | 6.560 ms |

Dependency import took **1222.1 ms** and index construction **971.5 ms**, excluded from warm samples. These values describe this one laptop run; they are not production telemetry.

## Exact measured source bytes

The report links six exact-byte snapshots next to itself: dataset, evaluator, metrics, production engine, model manifest and corpus. Snapshot hashes agree with report fingerprints. These snapshots retain original line endings and allow historical fingerprints to be checked after Git normalizes working-tree text. No environment files, credentials, provider configuration or model binaries are captured. Existing v1 snapshots and reports were not overwritten.

The snapshot paths are relative to the report location under `source.exact_byte_snapshots`. To verify a snapshot, SHA-256 its raw bytes and compare with the associated hash. To reproduce a historical measurement, restore those files to their repository paths and prepare the model artifacts listed in the captured manifest; later evaluator edits are not implied to reproduce historical fingerprints.

The expanded tests cover v1 default compatibility, selected-dataset hashing and CLI forwarding, balanced intent inventory, duplicate question rejection, tag summary denominators, complete-miss retention, null unsupported metrics and portable exact-byte snapshots. A source audit plus these tests improves traceability; it cannot establish annotation completeness or external validity.
