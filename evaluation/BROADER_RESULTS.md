# Multi-domain retrieval and real-answer diagnostic

Recorded on 25 October 2026. The [frozen bundle](broader_v1/README.md) contains six public repository-document snapshots across banking, cache behavior, realtime coordination, community workflows and durable jobs. It has 32 chunks and 60 authored questions. Source claims are documentation at immutable commits, not proof that each application has the described production capacity.

The development split contains 36 questions across banking/cache/realtime; the held-out query-domain split contains 24 across community/durable jobs. All source documents are indexed together. The query domains were frozen before rankings and no retrieval parameters were selected using the held-out results. A separate AI agent reviewed source labels before measurements; revision 2 retains five corrections and the original revision-1 files. These are not independently human-labelled or external benchmark data.

## Retrieval measurements

| Query split | Method | Top-five hits on answerable questions | MRR@5 | Recall@5 |
|---|---|---:|---:|---:|
| Development, 30 answerable | BM25 | 29/30 | 0.9250 | 0.8778 |
| Development, 30 answerable | Vector | 26/30 | 0.6039 | 0.8056 |
| Development, 30 answerable | Hybrid | 29/30 | 0.8750 | 0.9056 |
| Held-out domains, 20 answerable | BM25 | 20/20 | 0.8517 | 0.9458 |
| Held-out domains, 20 answerable | Vector | 18/20 | 0.6283 | 0.7833 |
| Held-out domains, 20 answerable | Hybrid | 20/20 | 0.8000 | 0.9458 |

BM25 ranks the first supporting passage earlier than hybrid on both splits. The hybrid development miss, `docwise-v3-029`, remains in the raw report. Unsupported questions receive no ranking-quality score: all methods return passages for them, which is not evidence of successful refusal. Thirteen of the 32 chunks exceed the production encoder's 256-token limit (maximum 785 tokens); the same production truncation behavior is preserved. Top-five hit does not establish complete answer coverage or semantic correctness.

[Development raw rankings and timings](results/broader-v1-dev-2026-10-25.json) · [Held-out raw rankings and timings](results/broader-v1-heldout-2026-10-25.json). Each report retains individual timing samples, exact measured source snapshots, the full frozen source bundle and annotation history. Local warm retrieval timings are not hosted service or model latency.

## Real-model run

The separately authorized sample used all 24 frozen held-out-domain questions, a maximum of 800 completion tokens per attempt, no automatic retries and one durable allowance ledger. The [raw answers](results/broader-v1-live-answers-2026-10-25.json) contain **24 actual provider attempts: 23 completed responses and one timeout**. The timed-out case `docwise-v3-046` was not retried. Its missing returned usage remains unknown. An explicit continuation started at the next distinct case after preserving the original report bytes, runner source and attempt prefix.

Twenty completed non-refusal responses passed retrieved citation-label membership. Three of four unsupported cases returned the exact refusal. The fourth (`docwise-v3-060`) answered that the catalog lab does not specify a payment processor and cited the source's scope boundary; it failed the strict exact-refusal decision proxy. That result is retained rather than changing the gold label or grader after observing it. Overall, 22 of 23 completed responses matched the pre-authored refusal decision. These checks do not evaluate entailment, completeness or human-rated answer quality.

Returned usage totals **48,320 prompt tokens and 2,373 completion tokens: 50,693 tokens** across the 23 responses with usage. Using the [documented GPT-6 Luna Standard prices](https://developers.openai.com/api/docs/models/gpt-6-luna), including reported cached input, their returned-usage estimate is **USD 0.0060185**. The timeout may have incurred charges not returned by the provider. This is not invoice reconciliation or a complete billed-cost total. No provider billing access or independent human grading is claimed.

The [review packet](broader_v1-review-packet.json) and [rubric/import workflow](HUMAN_REVIEW.md) are ready for external annotation. The packet omits gold labels and model outputs, but public gold remains accessible. There are **zero completed external human reviews**; the importer checks format/source consistency and explicitly cannot authenticate human identity or independence.

## Reproduce and verify

```powershell
python -m evaluation.broader --check-build --validate-only --check-encoder
python -m evaluation.broader --verify-report evaluation/results/broader-v1-dev-2026-10-25.json
python -m evaluation.broader --verify-report evaluation/results/broader-v1-heldout-2026-10-25.json
python -m evaluation.broader_answers --verify-report evaluation/results/broader-v1-live-answers-2026-10-25.json
```

These commands make no provider request. New retrieval runs require fresh output paths and retain full inputs. New paid answer runs require separate explicit authorization, a new operator-controlled allowance ledger and the `--live` flag. A failed attempt consumes its slot; `--resume` accepts only the same existing ledger and matching retained report, preserves prior rows and archives, and skips consumed cases. The ledger is a local execution guard, not an account billing cap. No additional calls are permitted by this already-exhausted allowance.

CI runs the offline tests and both frozen retrieval splits. The original two-guide corpus and its four historical reports remain unchanged. These measurements expand diagnostic diversity while preserving the distinction between authored evaluation, real provider outputs, injected-provider hosted checks, actual user outcomes and sustained production history.
