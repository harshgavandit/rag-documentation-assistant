# Verification record — 14 October 2026 (India time)

The configured local CLI RAG pipeline passed the live end-to-end verification. The provider model is `gpt-6-luna`. Credentials remain in the existing `.env`; none are included in this report.

| Check | Observed result |
|---|---|
| Regression suite | 14 tests passed with `python -m unittest discover -s tests -v` |
| Python dependencies | `pip check`: no broken requirements |
| Syntax compilation | `compileall` passed for application, demos, tests and verifier |
| Configuration | Required credentials and positive limits validated |
| OpenAI model access | Configured model retrieved successfully |
| Redis connectivity | Authenticated PING returned PONG |
| Document ingestion | 2 documents; 24 + 15 = 39 semantic chunks |
| Re-ingestion | 39 chunks after the second ingestion; no duplicate accumulation |
| Real generation | FastMCP answer cites section 7 of MCP_Documentation.md |
| Unsupported question | Exact refusal for Stripe API rate limits; no citations |
| Concurrent Redis admission | Limit 3: 10 attempts, exactly 3 admitted and 7 denied |
| Separate process | Same exhausted quota denied a second Python process |
| Early denial | Exhausted quota blocked before the graph/model path |
| Window expiry | New request admitted after the test window expired |
| Invalid Redis token | HTTP 401, query blocked |
| Credential scan | No configured key, token or endpoint value in the 35 scanned source/report files |
| CLI and demos | Help, chunk comparison, direct comparison script, ingestion and readiness succeeded |
| Character demo | 83 chunks in its own collection; live CLI query exited 0 with an answer and citation; semantic collection remained at 39 |

The default application limit is 20 admitted queries per 60-second window, scoped by application namespace, collection and model. The concurrency test uses its own expiring namespace and a limit of 3; it does not change `.env`, production limits, or unrelated Redis keys.

The first live attempt passed connectivity, concurrent admission and cross-process sharing, then failed the early-denial test because its first LangGraph/PyTorch import outlasted the 10-second test window. The verifier now imports the framework before starting that window. `live_attempt_1.json` retains this test-harness failure; `live_report.json` is the passing rerun and contains actual generated answers.

Local evidence files (generated outputs are excluded from GitHub; rerun the verifier to produce your own):

- `live_report.json`: timestamp, live invariant results and two real model answers.
- `live_attempt_1.json`: first attempt and test-harness timing failure.
- `local_health.json`: local configuration, document and collection readiness.
- `ingestion.txt`: second ingestion with the stable 39-chunk count.
- `chunking_comparison.txt` and `direct_demo_comparison.txt`: both runnable comparison entry points.
- `credential_scan.json`: credential-output scan result.
- `character_ingestion.txt` and `character_query.txt`: retained baseline ingestion and real CLI query output.
- `requirements.lock.txt` at the project root: validated dependency versions.

Repeat from the project root:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe main.py check --live
.\.venv\Scripts\python.exe -u verification/verify_live.py
```

The last command makes two generation calls and uses isolated expiring Redis counters. Source documents must be suitable for transmission to OpenAI. The inspected current corpus contains educational MCP guides and placeholder credentials; future document additions require their own handling decisions.

Verification covers this local command-line application and the configured external services. It does not establish load capacity, future provider availability, a deployed HTTP service, or a universal guarantee that all generated claims are grounded. Citation-label validation and the two observed question cases are the measured generation checks. The character ingestion/query branch also passed a live CLI query. Its baseline chunks lack section metadata, so its source labels use `Section: Unknown`; use semantic ingestion for precise section citations.
