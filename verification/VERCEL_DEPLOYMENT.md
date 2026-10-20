# Vercel deployment verification — 20 October 2026

Live production URL: https://rag-documentation-assistant.vercel.app

The `web/` application runs as a Vercel FastAPI function, with static assets served from Vercel's CDN. It uses a 39-chunk snapshot of the two educational Markdown documents. Retrieval combines BM25 and a pinned quantized MiniLM ONNX model with Reciprocal Rank Fusion. The original CLI code and ChromaDB workflow remain available separately.

## Observed checks

| Check | Result |
|---|---|
| Production deployment | Vercel reported READY; public production alias assigned |
| Unauthenticated HTTPS access | Homepage, JavaScript, CSS, document API and health endpoint returned 200 |
| Server configuration | Health returned ready, gpt-6-luna, 2 documents and 39 chunks |
| Real grounded query | FastMCP registration answered with a section 7 citation and 5 source passages; 9.47 seconds client-observed |
| Unsupported query | Exact Stripe rate-limit refusal, no citations; 2.44 seconds client-observed |
| Hosted Redis denial | Concurrent requests produced HTTP 429 responses with Retry-After; accepted request returned 200 |
| HTTP regression suite | 6 tests passed: static/library routes, validation, body/origin bounds, rate denial, Redis fail-closed and citation guards |
| JavaScript and Python syntax | node --check and compileall passed |
| Dependencies | pip check returned no broken requirements |

The concurrent test began while two earlier requests were still within the client window. Of four additional requests, one was admitted and three were rejected with Retry-After: 5. This verifies hosted quota sharing and denial behavior; it is not a load-capacity benchmark.

## Deployment configuration

- Vercel project: `harshgavandits-projects/rag-documentation-assistant`.
- Git root directory: `web`.
- Framework: FastAPI; build command: `python prepare.py`.
- Runtime: Python 3.12.
- Credential values are stored only as production server-side Vercel variables, with explicit user approval. They are absent from source control and client assets.
- Query admission limits: 3 per minute per client, 20 per minute shared and 100 per 24-hour shared window. Request limits do not constitute a provider billing cap.
- The build fetches immutable model/tokenizer artifacts listed in `web/model_manifest.json` and verifies SHA-256 checksums. Downloaded model artifacts are excluded from Git.
- Update `web/data/corpus.json` with `python scripts/export_web_corpus.py` after source-document edits, then redeploy.

## Resolved deployment failures

The first upload started inside `web` while the project expected a `web` subdirectory. Deployment now uploads from the repository root. The first completed build also tried to mount `public/` within the function, but Vercel moves this directory to its CDN. Assets now reside under `public/assets`, and the local static mount is conditional. Public model and corpus assets are explicitly included in the function bundle. The corrected production API passed real generation checks.

## Verification limits

The original 14-test CLI suite could not be rerun in the current Windows session: Application Control blocked NumPy's `_mt19937` DLL during dependency import. No application assertion ran in that attempt, and no security policy was bypassed. The CLI's earlier verification is recorded separately in `verification/VERIFICATION.md`; these deployment changes leave its source modules unchanged.

Local browser inspection earlier confirmed the page structure and document library. Final browser automation became unavailable because local browser use was disabled in Work settings. Final hosted verification therefore covers HTTP responses, assets, production generation and rate limiting; a final rendered-page/mobile visual inspection was not completed.

Full generated response records are retained locally in ignored `verification/vercel_live_report.json` and `verification/vercel_rate_limit.json`. No secrets are included in those records.
