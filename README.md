# Docwise — Hybrid RAG Documentation Assistant

A Python documentation assistant that combines keyword and vector retrieval, fuses their rankings, and generates answers with document/section citations. The repository contains a ChromaDB/LangGraph command-line pipeline and a lighter FastAPI/ONNX deployment of the same documentation workflow.

[Live app](https://rag-documentation-assistant.vercel.app/) · [Web implementation](web/app.py) · [Hosted verification](verification/VERCEL_DEPLOYMENT.md)

**Stack:** Python, FastAPI, LangGraph, ChromaDB, BM25, MiniLM, ONNX Runtime, OpenAI, Upstash Redis, Vercel.

## Try it

- How does FastMCP register a tool?
- What is the difference between Stdio and Streamable HTTP?
- How does MCP differ from traditional function tools?

The UI shows the answer, source labels, and expandable retrieved passages. The bundled library contains **Building a Custom MCP Server** and **AI Agents with Google Maps**: two educational Markdown guides, exported as 39 chunks for the current web app. There is no document-upload feature or saved conversation history. Questions and retrieved passages are sent to OpenAI for generation.

## Architecture

```text
CLI ingestion:
Markdown → heading-aware chunks → MiniLM embeddings → ChromaDB

CLI query:
Redis admission → LangGraph: hybrid retrieval → prompt → OpenAI → cited answer

Hosted query:
FastAPI validation → Redis client/shared admission
  → BM25 + pinned MiniLM ONNX vectors → Reciprocal Rank Fusion
  → top-5 passages → OpenAI → allowed citation-label check
  → answer + original passages + elapsed time
```

The two entry points share the document source but use different runtime/storage paths:

| Area | CLI | Hosted web app |
| --- | --- | --- |
| Corpus | Markdown files in `docs/`, indexed into ChromaDB | Exported `web/data/corpus.json` snapshot |
| Embeddings | Sentence Transformers | Quantized MiniLM ONNX model, attention-mask pooling and normalized vectors |
| Orchestration | LangGraph retrieval → prompt → generation nodes | FastAPI request handler |
| Retrieval | BM25 + ChromaDB vector ranks, fused with RRF | BM25 + in-memory vector ranks, fused with RRF |

Key implementation choices:

- **Preserve document structure:** [`semantic_splitter.py`](chunking/semantic_splitter.py) respects Markdown headings and fenced code; a separate character baseline makes the differences inspectable.
- **Fuse complementary retrieval:** [`hybrid_search.py`](retrieval/hybrid_search.py) and [`web/engine.py`](web/engine.py) combine lexical and semantic rankings rather than relying on one score alone.
- **Keep the hosted runtime small:** [`web/prepare.py`](web/prepare.py) downloads immutable model artifacts at build time and checks SHA-256 hashes from the manifest. Warm instances reuse the immutable retrieval index.
- **Bound public requests:** [`web/app.py`](web/app.py) enforces body/question limits, same-origin browser requests, Redis client/shared minute and daily admission, and safe dependency errors. Redis failure stops the query before retrieval/generation.
- **Check source labels:** Generated labels must match retrieved document/section labels. A recognized refusal carries no citations; missing or invalid labels reject the answer. This checks provenance labels, not whether every sentence is entailed.

## Run the web app locally

Use Python 3.12 and run these commands from the repository root:

```powershell
python -m venv .venv-web
.\.venv-web\Scripts\Activate.ps1
python -m pip install -r web/requirements.txt python-dotenv
Copy-Item .env.example .env
python web/prepare.py
python -m uvicorn app:app --app-dir web --env-file .env --host 127.0.0.1 --port 8000
```

On macOS/Linux, use `source .venv-web/bin/activate` and `cp .env.example .env`. The extra `python-dotenv` package lets Uvicorn load the local environment file; the hosted application reads server-side process variables directly.

Set the following in the untracked `.env` before sending a question:

| Variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | Server-side generation credential. |
| `UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN` | Required shared admission store. The application has no local rate-limit fallback. |
| `OPENAI_MODEL` | Generation model; current code default is `gpt-6-luna`. Use a model available to your provider project. |
| `OPENAI_MAX_COMPLETION_TOKENS` | Output bound; default `2048`. |
| `RATE_LIMIT_REQUESTS` | Hosted shared per-minute admission; default `20`. |
| `DAILY_QUERY_LIMIT` | Hosted shared 24-hour admission; default `100`. |
| `RATE_LIMIT_PREFIX` | Redis namespace; set a distinct value for a separate deployment. |

The hosted client limit is three queries per minute. Request budgets count admissions, not provider dollars. Open <http://127.0.0.1:8000>; `/api/health` reports configuration/corpus readiness without making a paid model request. Preparing the model needs network access to the pinned public Hugging Face artifacts.

## Run the CLI and chunking comparison

Use a separate environment for the larger ChromaDB/Sentence Transformers stack:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.lock.txt
python main.py compare
python main.py ingest
python main.py check
python main.py query "How does FastMCP register a tool with the MCP server?"
```

The CLI loads the root `.env` and uses the same OpenAI/Upstash variable names. `python main.py demo` runs comparison, semantic ingestion, and one query. To keep the baseline separate, use `python main.py ingest --strategy character` and `python main.py query "Your question" --strategy character`.

To add documents, place Markdown files in `docs/` and re-ingest. The web bundle does not update automatically: run `python scripts/export_web_corpus.py` in the CLI environment, then rebuild/redeploy the web app. Source text should be appropriate for transmission to the generation provider.

## Tests and verification

CLI environment:

```powershell
python -m unittest discover -s tests -v
python -m pip check
python main.py check --live
```

Web environment:

```powershell
python -m pip install httpx
python -m unittest discover -s web/tests -v
node --check web/public/assets/app.js
python -m compileall -q web
```

The deterministic tests cover chunk boundaries, RRF behavior, idempotent indexing, early rate denial, Redis failure, citation guards, payload/origin bounds, and public routes. `check --live` checks configured service connectivity/model access; it is not a generation evaluation. `python verification/verify_live.py` separately makes real generation requests and checks Redis admission using isolated expiring counters. That command consumes provider quota.

The [CLI verification record](verification/VERIFICATION.md) and [hosted verification record](verification/VERCEL_DEPLOYMENT.md) describe the dated checks and their limits. There is currently **no GitHub Actions workflow** in this repository; these commands are available to reproduce the checks locally.

## Scope and tradeoffs

The public app demonstrates a small, bundled documentation library rather than multi-tenant ingestion or large-corpus search. Cold starts build in-memory vectors from the exported chunks. Citation checks and a prompt-directed refusal are useful controls, not a universal grounding guarantee. The hosted report records a real answer/refusal pair and admission checks; it does not establish a broad answer-quality benchmark or sustained-load capacity.
