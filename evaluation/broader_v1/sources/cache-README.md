# Symposium RAG Bot — Shared Semantic Cache

A PDF question-answering application with page citations, exact and semantic answer reuse, and Redis request admission. The hosted FastAPI app shares a bounded cache across serverless instances; local development also supports SQLite and Valkey cache adapters.

[Live demo](https://symposium-rag-bot.vercel.app) · [Tests](https://github.com/harshgavandit/RAG-Semantic-Cache-Symposium-Bot/actions/workflows/tests.yml) · [Workload and recovery evidence](benchmarks/README.md)

**Stack:** Python 3.12, FastAPI, OpenAI, LangChain, FAISS, Upstash Redis, SQLite/Valkey, Streamlit, Vercel.

![The hosted chatbot showing its answer and document reference](evidence/vercel-desktop.jpg)

## What to explore

Ask about the date, venue, schedule, eligibility, competitions, or registration fees. Answers include page references; the interface also shows when an answer came from cache. The public demo uses a **fictional Innovate 2026 guide**, not a real event or private document. It does not search the web.

## Request and data flow

```text
PDF → page-aware chunks → OpenAI embeddings → FAISS + JSON metadata + hash manifest

Question → client admission → exact cache lookup
  ├─ hit: return saved answer and source references
  └─ miss: shared provider admission → query embedding → semantic cache lookup
       ├─ hit: reuse answer and original sources
       └─ miss: FAISS top-3 passages → OpenAI answer → citation check → cache
```

| Engineering choice | Mechanism |
| --- | --- |
| Shared cache | [`redis_cache.py`](phase2_retrieval/redis_cache.py) stores answers, vectors, source labels, expiry times, and bounded ordering in Upstash Redis. Lua updates perform insertion and eviction atomically. |
| Cache invalidation | [`resources.py`](phase2_retrieval/resources.py) derives the namespace from PDF, index and document-text/metadata hashes, embedding and answer models, threshold, and response-policy version. Changed citation metadata or the strict completion policy selects a new namespace, preventing stale cached answers from bypassing the updated context. |
| Admission before paid work | [`backend.py`](phase2_retrieval/backend.py) admits clients before lookup and uses a separate shared gate before query embedding. Exact hits avoid provider calls; semantic hits still require a query embedding. |
| Safe index loading | The loader checks corpus/index/metadata hashes and embedding dimensions, then reads FAISS plus JSON metadata rather than deserializing a pickle. |
| Answer boundary | Non-refusal answers require page labels present in the retrieved passages. Only a completed `stop` generation can enter the cache; truncated, filtered, tool-call and unknown completion states return explicit errors. |

Default cache settings are 256 entries and a 24-hour TTL per namespace. Semantic matching uses a cosine-distance threshold, so similar wording can reuse an answer. Citation-label checks validate page membership, not factual entailment.

The local Valkey adapter uses the same bounded hash/Lua contract as the Redis adapter, with a cosine scan of the configured entry limit. It reuses the query vector supplied by the handler, supports exact lookups before embedding, and does not require a vector-search module. This is a small bounded cache, not an indexed ANN service. Switching from the previous BetterDB adapter starts a new cache key layout; persisted document indexes remain unchanged. The legacy BetterDB package remains pinned in the dependency file but is no longer imported by the request path.

## Run locally

Use Python 3.12. From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

On macOS/Linux, use `source .venv/bin/activate` and `cp .env.example .env`. Configure the untracked `.env`:

| Variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | Server-side embedding and generation credential. |
| `LLM_MODEL`, `EMBEDDING_MODEL` | Defaults: `gpt-4o-mini`, `text-embedding-3-small`. Re-ingest when the embedding model changes. |
| `UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN` | Redis admission credentials; also required for `CACHE_BACKEND=redis`. |
| `RATE_LIMIT_ENABLED` | Defaults to `true`; invalid or unavailable Redis blocks admission. |
| `CACHE_BACKEND` | `local`, `valkey`, `auto`, or `redis`. `auto` tries Valkey and falls back to local SQLite; Vercel requires `redis`. |
| `CACHE_THRESHOLD`, `CACHE_TTL_SECONDS`, `CACHE_MAX_ENTRIES` | Defaults: `0.15`, `86400`, and `256`. |

The remaining timeout, request-budget, and Valkey options are in [`.env.example`](.env.example). For a local run without Redis, explicitly set `CACHE_BACKEND=local` and `RATE_LIMIT_ENABLED=false`; this disables shared admission rather than simulating it.

Create the included contact-free sample, then build the index:

```powershell
python scripts/create_sample_pdf.py
python -m phase1_ingestion.ingest
python -m uvicorn phase2_retrieval.backend:app --host 127.0.0.1 --port 8000 --no-proxy-headers
```

Open <http://127.0.0.1:8000> for the browser UI. In a second terminal, `python -m streamlit run phase2_retrieval/frontend.py --server.port 8502` starts the alternate local interface. `BACKEND_URL` defaults to `http://127.0.0.1:8000`.

For your own document, put an appropriate PDF at `data/symposium.pdf` before ingestion. The sample script refuses to overwrite an existing file. Ingestion sends extracted text to the configured embedding provider; PDFs, extracted data, indexes, local caches, and credentials are excluded from Git. Rebuild the index after changing the PDF or embedding model.

Windows shortcuts: `./run.ps1 ingest`, `./run.ps1 backend`, `./run.ps1 frontend`, and `./run.ps1 test` use the repository's `.venv`.

## Tests and verification

```powershell
python -m pytest -q
python -m pip check
python -m compileall -q config.py app.py phase1_ingestion phase2_retrieval scripts tests
```

The offline suite covers cache reuse and misses, eviction and TTL, Redis cache behavior, source preservation, refusal/citation handling, index validation, rate denial, and hosting configuration. [GitHub Actions](.github/workflows/tests.yml) runs the regression suite on Python 3.12 without a paid provider request.

With the included fictional sample indexed, OpenAI and Redis configured, and `RATE_LIMIT_ENABLED=true` (restart the backend after configuration changes):

```powershell
python scripts/verify_live.py --url http://127.0.0.1:8000
```

This makes real embedding/generation requests, exercises cache behavior and HTTP errors, and uses isolated expiring Redis counters to check atomic admission and recovery. It writes a local verification report and consumes provider quota.

The [20 October hosted report](evidence/vercel-verification-report.md) records observed provider, cache, citation, and denial checks. It reports a one-page, one-chunk public sample; that demonstration does not establish retrieval quality for a larger corpus. No cost-savings or throughput figure is claimed.

## Workload and recovery measurements

The [benchmark guide](benchmarks/README.md) contains reproducible commands and retained reports. A local SQLite workload completed **9,000 operations** across three trials at concurrency 1, 4 and 8 with zero operation errors or expected-result mismatches. Four workers outperformed eight on this machine, so the report retains the contention tradeoff rather than claiming linear scaling.

The workload exposed unclosed SQLite handles; explicit connection closure now preserves transactions and rollback, with regression tests. Six adapter recovery checks and 18 isolated HTTP observations cover TTL refill, bounded eviction, writer contention, unavailable dependencies and recovery. The HTTP drill uses injected providers. A separate **real Redis drill** used 15 commands against isolated expiring keys: exactly three of 12 concurrent attempts were admitted, identities remained separate, and admission resumed after expiry. These are controlled development measurements, separate from the hosted Redis cache and production traffic.

## Deployment and limits

The [deployment guide](docs/vercel-deployment.md) explains the Vercel build and shared Redis cache. The build generates a fictional sample under `deploy_data/`, keeping original documents and local runtime data out of the upload. Streamlit remains a separate local interface.

Semantic cache reuse is heuristic and can conflate nearby intents. The cache performs a bounded vector scan rather than a distributed vector-index query. Request budgets limit request counts, not total provider spending; production use would need corpus-specific evaluation, traffic-aware budgeting, and monitoring.
