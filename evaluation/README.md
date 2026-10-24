# Docwise retrieval evaluation

The [multi-domain results](BROADER_RESULTS.md) extend the evaluation to frozen documentation from five application domains with development and held-out query-domain splits. They include 60 retrieval cases, 24 additional real provider attempts, raw outputs and a ready-to-use external [source-label review packet](HUMAN_REVIEW.md). Original results below remain historical; datasets and scopes are not combined into one accuracy score.

The latest [expanded evaluation](METHODOLOGY_V2.md) covers 112 authored intents, with source labels checked by a separate AI agent before ranking. [Security/conflict/ambiguity challenges](ADVERSARIAL.md) record twelve additional real model calls. The original 50-case run and its 18 model outputs below remain historical evidence, with original data and results preserved.

This is a synthetic, repository-authored diagnostic set built from the two bundled educational documents. It is **not an external benchmark, a held-out research test, production traffic, or a measure of generated-answer correctness**. Questions and initial gold labels were authored before running retrieval. Independent source review then corrected omitted supporting chunks under the existing partial-evidence policy, recorded as annotation revision 2 before generation-provider evaluation. It also clarified case044 to ask about replacing SQLite with PostgreSQL. The fixed live subset and fact patterns were preserved. Production retrieval parameters and code were not tuned against these cases.

`cases.json` freezes 50 questions: 44 answerable (22 about the Maps guide, 22 about the healthcare guide) and 6 unsupported. Cases cover exact terms, paraphrases and questions requiring evidence from multiple sections. Each answerable case includes stable chunk IDs, binary relevance labels and verbatim supporting excerpts. A chunk is relevant when it supports at least one requested answer component; recall measures the fraction of labeled relevant chunks retrieved. Alternative evidence chunks may support the same answer component, so chunk recall does not measure complete answer coverage. Some overlapping summaries are also labeled. Gold judgments are authored and may omit other acceptable sources.

Chunk IDs use `<document filename>::<document-local chunk_index padded to 3 digits>`, for example `MCP_Documentation.md::007`. They do not use the position in the combined JSON array. Heading-only and table-of-contents chunks are not gold answer evidence. The corpus has 39 chunks; its frozen byte SHA-256 is `9a8242dcc4309057921dcc635783cf8415b4012155704878ad58ad15329620a7`. Changes to the corpus require reviewing and versioning the gold set; the CLI rejects a mismatched hash, unknown IDs, duplicate IDs and nonmatching evidence excerpts.

## Run locally

Use Python 3.12 with the public web dependencies. Model preparation downloads checksum-verified public artifacts pinned in `web/model_manifest.json`; evaluation itself makes no network or generation-provider calls and requires no credentials.

```powershell
python -m pip install -r evaluation/requirements.lock.txt
python web/prepare.py
python -m evaluation.run --validate-only
python -m unittest discover -s web/tests -p test_evaluation.py -v
python -m evaluation.run --output evaluation/results/local.json --repeats 3 --warmup-rounds 1
```

The command exits nonzero on validation or execution failure. A successful run writes a JSON report with schema version, Git HEAD and dirty-state flag, evaluator/engine/dataset/corpus hashes, verified model revision and artifact hashes, Python/package/CPU execution settings, raw per-case rankings, raw timing samples and aggregate metrics. Reports omit environment variables, credentials, user directories and repository remote URLs. `working_tree_dirty: true` means the recorded Git HEAD is the base revision; recorded source hashes identify the actual evaluator and retrieval code used.

Hashes cover exact file bytes, including line endings. The [source snapshot directory](results/sources) retains the original Windows-run dataset, retrieval runner, metric functions, web engine and answer handler, as well as the generation runner. Git attributes preserve these snapshots unchanged. Working source uses LF across platforms; normalizing line endings changes a byte hash without changing Python behavior. The corpus keeps its original bytes so dataset validation is portable.

## What is compared

- **BM25:** the production index's BM25 scores with identical tokenization, positive-score filtering and corpus-order tie handling, returning at most five results.
- **Vector:** the production index's pinned quantized MiniLM ONNX encoder, attention-mask mean pooling, normalization and cosine ordering, returning five results. The encoder truncates at 256 tokens, as production does.
- **Hybrid:** calls the unchanged production `web.engine.Index.search(question, top_k=5)`. It uses Reciprocal Rank Fusion with constant 60 and up to ten candidates from each leg. This is the deployed web retrieval path; it does not evaluate the separate ChromaDB CLI retriever.

Each answerable query receives hit@1, hit@5, reciprocal rank of its first relevant result through rank five, recall@5 over all gold chunks, and binary nDCG@5. nDCG uses gains of one for relevant chunks, `1/log2(rank+1)` discounts, and an ideal ranking with `min(5, number of gold chunks)` relevant results. Summary scores are macro means over the 44 answerable queries. Missing relevant results score zero. Unsupported cases receive diagnostic rankings and a returned-nonempty count, with ranking metrics set to null: the retrieval engine always returns results and has no abstention threshold.

Startup dependency import, index/model construction and the first query for each method are reported separately from warm measurements. There is one constructed index in a fresh evaluator process; methods run in the fixed order BM25, vector, hybrid. The first query for later methods shares the index and may benefit from earlier calls. The command does not clear OS or filesystem caches and does not claim a cold-machine or Vercel cold-start benchmark. It excludes the first method calls and one complete warmup pass, then records three measured repeats per case by default. Rankings must agree across repeats. Percentiles use linear interpolation at `(sample_count - 1) * p`. Warm latency covers all 50 cases, so each method has 150 samples in the recorded run. HTTP, network, rate limiting, generation and model download time are excluded.

## Recorded local result

The committed [raw retrieval report](results/local-retrieval-2026-10-24.json) was measured on 2026-10-24 with Python 3.12.14 on Windows 11, CPUExecutionProvider (two intra-op threads, one inter-op thread), and model revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`. The report records exact package versions and artifact hashes. Its dataset SHA-256 is `90778a954c73dde7e1e2dcbbb3a2b7aa6422be2a5214bb5a1d1b9c69aeb62df3` (annotation revision 2).

| Method | Hit@1 | Hit@5 | MRR@5 | Recall@5 | nDCG@5 |
|---|---:|---:|---:|---:|---:|
| BM25 | 0.9091 | 0.9545 | 0.9318 | 0.8902 | 0.8825 |
| Vector | 0.5909 | 0.9545 | 0.7432 | 0.8636 | 0.7358 |
| Production hybrid | 0.8864 | 0.9545 | 0.9205 | 0.8883 | 0.8775 |

BM25 ranked the first relevant chunk above hybrid more often on this small authored set. All three retrieved some labeled evidence for 42 of 44 answerable cases. Hybrid missed all gold chunks for `docwise-027` (the four healthcare tool implementation files) and `docwise-039` (FastMCP schema generation). All six unsupported questions still received nonempty rankings under every method. These are observed limitations, with no retrieval changes made to improve the reported scores.

Local hybrid warm p50 was 2.05 ms and p95 was 3.23 ms in this run. Dependency import took 997 ms and index construction took 1,051 ms, reported separately. These timings describe this machine and procedure; they are not live application latency or an SLA. Re-run the CLI to measure another environment; timing values will vary.

The dataset also marks a fixed generation sample: 12 answerable cases (six per document) and all six unsupported cases. `required_fact_patterns` are transparent regex proxies for a few required facts; they do not establish full semantic correctness. The separate generation evaluator and its recorded evidence describe whether generation-provider calls were actually run and what they observed.

## Measured model answers

The [retained answer report](results/live-answers-2026-10-24.json) records **18 real generation requests** through the unchanged production `answer()` function, using actual local hybrid retrieval and the configured `gpt-6-luna` model. These are public-document questions; no private documents were submitted. This run measures retrieval plus generation, excluding the hosted HTTP and Redis admission paths. It is a small authored diagnostic sample, not a production accuracy estimate.

| Check | Observed result |
|---|---:|
| Requests completed / attempted | 18 / 18 |
| Answerable cases with non-refusals and allowed retrieved source labels | 12 / 12 |
| Answerable cases matching every pre-authored fact regex | 11 / 12 |
| Unsupported cases returning the production refusal | 6 / 6 |
| Combined fixture checks passed | 17 / 18 |
| Provider-reported prompt tokens | 27,415 |
| Provider-reported completion tokens | 1,216 |
| Provider-reported total tokens | 28,631 |

Case `docwise-034` failed the fact proxy because the answer says diagnoses appearing in two or more visits, while the frozen regex expects `recurr`. The retained answer contains the requested idea. The original pattern and failed check remain unchanged; adjusting a fixture after observing its answer would conceal the proxy's limitation. Citation checks establish membership in retrieved source labels, not sentence-level factual entailment. This set contains no prompt-injection cases and supports no adversarial-robustness claim.

Run a **new, explicitly opted-in** sample with a new output filename:

```powershell
python -m evaluation.evaluate_answers --live --max-requests 18 --env-file .env --output evaluation/results/answers-new.json
```

This requires the existing `OPENAI_API_KEY`; an optional private env file supplies only the generation key, model and output-token bound. It makes at most the requested number of calls (hard maximum 30), does not retry, saves progress after every case, and stops on dependency failures. There is no request without `--live`. Reports retain the responses, model identity, timings, numeric usage fields and individual grading results. Currency cost is left unknown rather than inferred from unverified prices. Never commit the env file.

The exact runner used for the recorded sample is retained as a [source snapshot](results/sources/evaluate_answers-2026-10-24.py.txt); its SHA-256 matches `source_sha256["evaluation/evaluate_answers.py"]` in the report. Subsequent fixes in the current runner report missing usage as unknown, with known subtotals separately, and reject existing or invalid output destinations before provider work. All 18 recorded responses have complete usage, so the retained token totals are unaffected. New outputs are reserved exclusively; interrupted runs retain partial progress. These fixes needed no additional provider requests.

## Continuous checks

[GitHub Actions](../.github/workflows/evaluation.yml) installs the pinned evaluation environment on Python 3.12, verifies the public model checksums, runs web/evaluation regressions, validates the authored source excerpts and executes the retrieval comparison. It retains a JSON artifact for 30 days. CI has no API credentials and runs no paid generation. Ranking/runtime failures fail the job; scores and timings are reported without fitting an arbitrary threshold to this dataset.

Verify the committed historical evidence separately, without models, credentials or network access:

```powershell
python -m evaluation.verify_evidence
```

This checks all four recorded reports against their exact-byte source and dataset snapshots. It recomputes per-question ranking metrics, aggregate scores, timing percentiles, generation fixture checks, and returned-token totals from retained raw records. CI also runs this verifier and corruption regressions. Older report schemas retain their original fields; the verifier does not rewrite them or apply a newer missing-usage policy retrospectively. This detects inconsistent evidence and accidental drift. It cannot independently attest that a provider call occurred, reproduce historical hardware timings, or convert authored regex checks into human evaluation.
