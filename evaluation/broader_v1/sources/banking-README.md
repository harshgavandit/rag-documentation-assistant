# Banking Agent Harness

A Python banking assistant that separates language-model suggestions from transfer authorization. The model can look up an account or propose a transfer; application code validates the proposal, requires confirmation in a later user turn, and records the simulated outcome.

[Live demo](https://banking-agent-harness.vercel.app) · [Tests](https://github.com/harshgavandit/banking-agent-harness/actions/workflows/tests.yml) · [Hosted verification](verification/VERCEL-DEPLOYMENT.md)

**Stack:** Python 3.12, FastAPI, Google ADK, LiteLLM, OpenAI, Upstash Redis, Vercel.

![A confirmed simulated transfer with its resulting account balance](verification/browser-confirmed-transfer.jpg)

## Try the workflow

```text
What is my savings account balance?
Transfer 1000 rupees to Aaron.
confirm
Check transaction TXN001.
```

- Check balance, daily transfer allowance, and existing transactions.
- Find beneficiaries by name, ID, or the last four account digits. Ambiguous matches require a choice; blocked beneficiaries are rejected.
- Review recipient and amount before approving a staged transfer in a separate message or with **Confirm transfer**.
- Refresh the page and continue the same demo session. Other visitors receive separate accounts and histories.

All accounts and transactions use mock data. No real money moves.

## How a transfer runs

```text
Browser + signed session cookie
  → FastAPI: origin / payload checks + Redis session lease
  → ADK agent: bounded model/tool calls
  → Deterministic policy: beneficiary, balance, amount, daily allowance
  → Pending proposal: no debit until a later explicit confirmation
  → Revalidate → simulated executor → recorded transaction outcome
  → Atomic Redis snapshot commit → updated account and tool results
```

The important boundaries live in code:

| Decision | Implementation |
| --- | --- |
| Transfer eligibility | [`policy_service.py`](app/services/policy_service.py) checks finite positive amounts, beneficiary status, balance, and allowance independently of the prompt. |
| Confirmation | [`banking_tools.py`](app/tools/banking_tools.py) requires a previously staged proposal and an explicit confirmation from a different invocation. It expires old proposals and revalidates before execution. |
| Actual outcome | [`transaction_executor.py`](app/services/transaction_executor.py) supplies `SUCCESS`, `FAILED`, or `PENDING`. Failed transfers leave the balance unchanged; pending transfers hold funds. |
| Visitor isolation | [`hosted_store.py`](app/hosted_store.py) keeps one Redis snapshot per visitor with a 120-second lease, compare-owner commit, and 24-hour inactivity expiry. |
| Bounded execution | [`agent.py`](app/agent.py) applies Redis user/model admission and tool limits. The hosted runner bounds each turn to 75 seconds and eight model calls. |
| Request boundary | [`web.py`](app/web.py) validates declared lengths and caps actual streamed POST bodies at 12,000 bytes before parsing or calling a handler. Missing, chunked or falsely small length headers cannot bypass the byte limit. Error responses retain the API's no-store and browser security headers. |

## Run locally

Use Python 3.12. From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.lock.txt
Copy-Item app/.env.example app/.env
```

On macOS/Linux, activate with `source .venv/bin/activate` and copy with `cp app/.env.example app/.env`. Set values in the untracked `app/.env`:

| Variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | Server-side OpenAI credential. |
| `LLM_MODEL` | LiteLLM model identifier; default `openai/gpt-4o-mini`. |
| `UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN` | Required for the public-style app's snapshot storage and for enabled admission limits. |
| `DEMO_SESSION_SECRET` | Random signing secret of at least 32 characters for the public-style app's cookie. |
| `RATE_LIMIT_ENABLED` | Set `true` to enforce the Redis limits; Redis failures then block agent execution. |
| `MOCK_TRANSACTION_OUTCOME` | Optional `SUCCESS`, `FAILED`, or `PENDING` to exercise different results. |

See [`app/.env.example`](app/.env.example) for timeouts, request limits, and tool/transfer budgets. Keep credentials server-side.

Run the same FastAPI interface used by the deployment:

```powershell
python -m uvicorn app.web:app --host 127.0.0.1 --port 8011
```

Open <http://127.0.0.1:8011>. The original local ADK developer interface is also available with `python scripts/serve.py --port 8010` or `./start.ps1`; its in-memory sessions are separate from the Redis-backed web app. The developer interface is not mounted on the public deployment.

## Tests and reproducible checks

```powershell
python -m pytest -q
python -m pip check
python -m compileall -q app scripts tests index.py
node --check web/app.js
```

The offline suite covers policy rejection, later-turn confirmation, execution outcomes, visitor isolation, signed cookies, cross-origin rejection, overlapping turns, and actual ASGI body limits. Request-boundary tests include malformed and duplicate lengths, zero-prefixed length overflow, chunked input, false length claims and exact-limit byte preservation. GitHub Actions runs it on Windows and Linux with Python 3.12; tests use doubles rather than paid provider requests.

With the local web app running and credentials configured, `python scripts/verify_hosted.py http://127.0.0.1:8011` exercises real model calls, restored proposals, and separate visitor accounts. It consumes provider quota and changes only synthetic demo account state. The separate `scripts/verify_live.py` and `scripts/verify_http.py` check the agent and ADK developer interface.

The [20 October hosted report](verification/VERCEL-DEPLOYMENT.md) records the deployment checks and their limits. It is a dated observation, not a load-capacity or banking-compliance claim.

## Scope

This is an anonymous-session engineering demonstration, without identity-verified accounts or payment rails. Snapshot expiry resets the demo; the daily allowance is a session budget without a midnight reset. Redis serialization protects this mock ledger, not an external financial transaction. Real banking would additionally require durable financial ledgers, authenticated identities, reconciliation, and operational controls.
