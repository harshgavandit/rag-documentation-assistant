# Source-label review

The broader diagnostic has AI-authored labels checked by a separate AI agent. No independent human review has been completed or invented. This workflow makes a frozen review package usable by an external reviewer without changing that fact.

The packet omits gold labels, original case IDs, method rankings and model answers. It contains the questions and all 32 source chunks. Public gold remains available in this repository, so this is not guaranteed blinding or a controlled research study. An independent reviewer should label before opening the published answers and should disclose any prior exposure.

## Rubric

For each question, judge answerability against **only the supplied sources**. A relevant chunk supports at least one requested proposition; label every known source alternative, including partial support, rather than only the best result. Heading-only mentions and question premises are not supporting evidence. Provide a verbatim excerpt for every relevant chunk. For unsupported questions, leave the labels and evidence empty and explain what the sources do not establish. Distinguish a documented claim from proof that an implementation works.

Use this response shape with the packet's exact ID and all of its question IDs:

```json
{
  "schema_version": 1,
  "packet_id": "<packet SHA-256>",
  "reviewer": {
    "public_id": "<public reviewer handle>",
    "human_attested": true,
    "independent_attested": true,
    "used_ai": false
  },
  "answers": [{
    "case_id": "<blind question ID>",
    "answerable": true,
    "relevant_chunk_ids": ["<document>::000"],
    "evidence": [{"chunk_id": "<document>::000", "excerpt": "<verbatim source text>"}]
  }]
}
```

For an unsupported answer, add `"unsupported_reason": "<scope explanation>"`. Do not include private email, credentials or employer material. The reviewer identity and independence are self-attestations; the importer cannot authenticate them. A source-format check must never be described as completed independent human validation by itself.

## Reproduce and validate

```powershell
python -m evaluation.human_review --dataset evaluation/broader_v1/dev.json --dataset evaluation/broader_v1/heldout.json --corpus evaluation/broader_v1/corpus.json --output review-packet-new.json
python -m evaluation.human_review --packet review-packet-new.json --review /private/reviewer-labels.json --output review-validation-new.json
```

Both commands refuse to overwrite earlier files. Validation rejects altered packet hashes, missing/duplicate cases, unsupported source IDs, nonmatching excerpts and absent human/no-AI attestations. It reports `identity_or_independence_verified: false` even when format checks pass. Do not commit a received review without permission to publish the reviewer's handle and annotations.

After an actual review arrives, retain its original bytes and compare disagreements by question and source. Record any adjudication, prior exposure and annotation revision. Publish a separate new result if labels change; preserve the original frozen results. A reviewer is allowed to disagree with the authored labels. Do not overwrite the original scores or tune retrieval on the held-out query domains.
