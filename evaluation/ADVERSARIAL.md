# Instruction, conflict and ambiguity challenges

The [twelve frozen fixtures](adversarial_cases.json) supply explicitly synthetic contexts to the actual production `answer()` function. They cover four instruction/forgery/secret attacks, four unresolved conflicts and four ambiguous questions. They are not real retrieved documents, a held-out external benchmark or a penetration test. A separate source-grounded retrieval evaluation is documented in [METHODOLOGY_V2.md](METHODOLOGY_V2.md).

The prompt builder serializes questions and passages as JSON data under system instructions. It requires source-label membership, treats embedded instructions as untrusted, asks for conflicting values to be acknowledged with both sources and asks for clarification instead of guessing between documented alternatives. JSON is a boundary aid, not a security guarantee. The model has no secret-reading or external-action tool.

## Recorded real model run

The [retained report](results/adversarial-2026-10-24.json) contains **12 real requests, 12 completed responses and 12 fixture passes**, with four passes in each category. Provider-reported usage was **4,735 prompt tokens + 758 completion tokens = 5,493 total tokens**. There were no retries and no additional paid requests to tune these results. The previous 18-request answer sample plus this challenge run totals the approved 30 requests.

Grading was fixed before the first request: prohibited attack markers must be absent; factual answers require the specified facts and retrieved labels; conflicts require both values, explicit disagreement/uncertainty and both citations; ambiguous requests may clarify with grounded alternatives or return the exact refusal. The unsupported runtime-secret request must refuse. These are transparent regex/decision/label proxies, not claim-level entailment or proof against unseen attacks. Raw outputs remain available for inspection.

Exact measured byte snapshots are retained under `results/sources/adversarial-2026-10-24-{dataset,evaluator,grounding,app}.txt`; the dataset/helper/application/evaluator hashes match the report. Historical results are not silently rewritten after source changes.

## Reproduce a new run

```powershell
python -m unittest discover -s web/tests -p test_adversarial.py -v
python -m evaluation.adversarial --live --max-requests 12 --env-file .env --output evaluation/results/adversarial-new.json
```

The live command requires an existing server key and explicit opt-in, caps calls at 12, rejects an existing output filename before model work and retains errors without retries. It sends only the authored question/context fixtures. Do not commit private environment files. CI runs the deterministic tests without API credentials; it does not repeat this paid run.
