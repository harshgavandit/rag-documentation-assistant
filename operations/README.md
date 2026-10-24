# Operations evidence and answer feedback

Docwise can retain small aggregate measurements in the same configured Upstash Redis service used by public-demo admission. Collection is explicitly enabled by `OPS_ENABLED=1`; it creates no separate backend or database. This folder's collector performs only two read-only HTTP requests: public health and protected seven-day aggregates. It never asks a question, invokes an embedding/model provider, creates an issue or sends a message.

The hosted setup is configured by the maintainer: production enablement, a generated server-only read token, the Actions secret and target origin. Public daily artifacts retain endpoint timing/readiness and aggregate-schema validation only. Full measurements remain behind the authenticated endpoint. Daily checks begin accumulating observations after enablement; they cannot establish an earlier uptime history.

## Recorded enablement and controls

Monitoring was enabled on 24 October 2026. The first [read-only workflow run](https://github.com/harshgavandit/rag-documentation-assistant/actions/runs/40099653359) passed. Its [public health/schema artifact](results/public-health-2026-10-24.json) contains no private usage, token, feedback or cost totals. Future daily runs create dated observations; a single successful check is not an uptime history.

The [live controls report](results/live-controls-2026-10-24.json) records six passing observations against actual Redis and deployed HTTP routes: unauthorized reads denied, authorized reads accepted, signed synthetic tickets issued, feedback accepted once with replay rejected, real ticket expiry rejected, and synthetic fixture events/feedback kept in their own cohort. No query endpoint or model call was exercised in this drill. Its two adapter events and one feedback click are explicitly synthetic, not real-user outcomes.

Reproduce a separately authorized control drill with private existing configuration files:

```powershell
python operations/verify_live.py --live --env-file .env --control-file /private/operations.env --output operations/results/controls-new.json
```

The control file contains only `OPS_READ_TOKEN`. This command writes two signed synthetic fixture events, consumes one test ticket through the deployed feedback endpoint and shortens only the second exact synthetic ticket's TTL to verify expiry. It uses no broad key lookup/deletion and never calls a model. It must not be used as real visitor or task-success evidence.

## Server configuration

Configure these server-only variables alongside the existing Upstash REST URL and token:

| Variable | Meaning |
| --- | --- |
| `OPS_ENABLED=1` | Enable best-effort query aggregate writes and feedback; unset or any other value leaves collection disabled |
| `OPS_READ_TOKEN` | Random secret of at least 32 characters; use a long generated token, never put it in client JavaScript, URLs or committed files |
| `OPS_PREFIX` | Optional isolated Redis namespace, default `docwise:operations:v1`; only letters, digits, colon, underscore and hyphen, at most 80 characters |
| `OPS_PROMPT_USD_PER_MILLION` | Optional explicit nonnegative prompt-token price used for recorded-token estimates |
| `OPS_OUTPUT_USD_PER_MILLION` | Optional explicit nonnegative output-token price; both rates must be valid before any estimate appears |

The aggregate route is `/api/operations?days=7`. The route requires `x-ops-token` equal to the configured secret using `Operations.is_authorized`; the helper rejects absent/short server secrets and checks equality with `hmac.compare_digest`. An unsigned request cannot designate itself synthetic. A valid query carrying the same header is recorded in the separate `synthetic` cohort. All other query events enter `visitor`, which means unsigned requests and can include crawlers, abuse or unmarked tests. It is not a count of verified people. The route must remain unavailable when collection/auth configuration is absent, and must never return a public metrics page.

`Operations()` reads environment values when used, keeping startup backward compatible and tests independent of module import order. `begin_request(header)` sets a request-local `ContextVar`; `current_event()` lets the existing two-argument answer function attach returned provider usage without changing its public signature. `record_usage(event, result.get('usage'))` must run as soon as an actual provider response returns, before answer guards. `finish_request(event, status_code, outcome)` resets the context and catches collection failures. The query's answer/status continues even if Redis telemetry is unavailable. Context is local to the current thread/task; no mutable global event, question or identity is stored.

## What is retained

UTC daily hashes contain fixed-name integer counters only: handler query attempts; 2xx/4xx/5xx classes; answered/refused/errors; cumulative latency buckets; duration sum; returned provider-response counts; complete/missing usage counts; actual provider-reported prompt/output tokens; issued feedback tickets; and helpful/unhelpful/category counts. Latency buckets are cumulative at 100, 500, 1,000, 3,000, 10,000, 30,000, 60,000 and 300,000 ms. Individual durations are capped at 300,000 ms and measured before the telemetry write.

No question, answer, source excerpt, IP address, user-agent, email, account identifier, raw prompt, token content or provider response text is passed to the telemetry API, retained in Redis or written to collector artifacts. Feedback offers a boolean and one category from `clear`, `incomplete`, `incorrect`, `citation_issue`, `other`; there is no free-text field. The existing admission system may use its own identity hash; observability receives none of that identity data.

Each successfully answered request can receive a random 256-bit, 43-character feedback ticket. Redis stores only its SHA-256 key and `visitor`/`synthetic` scope value for one hour. The query's single Lua write increments aggregates and issues its ticket together; refusal/error responses do not receive tickets. Ticket issuance is capped at 1,000 per scope per UTC day. Feedback Lua checks an aggregate 600-attempts/minute rate limit, consumes a valid ticket atomically once, and adds fixed counters to the ticket's cohort on the feedback submission day. It caps accepted feedback at 10,000 per scope/day. A replay or expiry returns `expired_or_used`; dependency failure returns `unavailable`. Tickets are not person or session identifiers, and the same person can obtain multiple tickets by making multiple admitted queries.

Daily hashes expire 30 days after their last write. Query counters are capped at 1,000,000 handler events per cohort/day. Since only fixed fields exist, memory per daily hash does not grow with question variety. Ticket keys expire after one hour, and minute rate-limit keys expire after 60 seconds. No `KEYS`, `SCAN`, broad deletion, query log, per-user profile or unbounded series is used. A seven-day snapshot reads at most 14 explicitly constructed daily keys in one Redis REST pipeline. Collection uses one best-effort Redis EVAL per query, a 0.75-second network socket timeout, no retry, and a 64 KiB response limit. This is a socket timeout rather than a precise end-to-end wall-time SLA; local DNS/connection phases can add overhead.

Missing usage is reported without guessing token counts. Provider exceptions that return no usage cannot establish actual billed usage. Dollar estimates remain absent unless both explicit rates are configured; when present, they cover recorded returned usage only, exclude unobserved failed/missing usage, and are not invoice reconciliation. Rates apply to the selected snapshot's recorded tokens, so use stable, correct model-specific rates and record any configuration changes separately. Request counts are never converted into dollars.

## Collect a protected snapshot

Use a local environment variable or secret manager for the token; never put its value in command history or the URL. With `OPS_BASE_URL` set to the HTTPS site origin and `OPS_READ_TOKEN` supplied securely:

```powershell
python operations/collect.py --output operations/snapshot-new.json
```

Python's standard library is sufficient. The output must be a new `.json` file in an existing directory. The collector does not overwrite earlier evidence, follows no HTTP redirects with the token, bounds response size, and reconstructs artifacts using numeric allowlists rather than copying arbitrary server text. By default it retains health HTTP status/readiness, observed aggregates, collection UTC time and target hostname for the owner's private local report. Keep that full report private; do not commit it or upload it as a public Actions artifact. It does not record the read token, request headers or server error messages. Endpoint 401/404/503, malformed data or health failure produces a safe failure artifact and exit code 1. Missing/short credentials, invalid origins or an existing output file fail validation with exit code 2. These states must not be described as successful monitoring or zero traffic.

For an artifact that may be publicly accessible, use:

```powershell
python operations/collect.py --public-health-only --output operations/health-check-new.json
```

This mode still fetches and validates the protected aggregates and requires both healthy public readiness and valid private schema to pass. The saved artifact contains only public health status, the boolean `aggregate_schema_valid`, safe endpoint/schema failure states, collection time, target hostname and the overall pass boolean. It omits daily/total query counts, token usage, feedback counts, pricing and all other private aggregate values. Schema errors remain failures; excluding measurements does not skip the private schema check. Full aggregates remain in protected Redis and authenticated owner responses, and are never printed by the workflow.

The optional GitHub workflow in `.github/workflows/operations.yml` runs at 03:25 UTC daily or by manual dispatch. Configure repository variable `OPS_BASE_URL` and Actions secret `OPS_READ_TOKEN` before enabling/running it. It has only `contents: read`, makes no paid-provider call, changes no repository contents and retains a workflow artifact for 30 days. Public repository Actions artifacts may be accessible to other people, so this workflow always passes `--public-health-only`. Its artifact exposes only health, schema validity and safe failure states, never private aggregate measurements. A missing configuration fails visibly; the workflow does not silently manufacture an empty-success report. Running on a schedule provides dated health/schema observations only after successful runs have actually accumulated; the configuration itself does not prove operational history.

## Interpreting evidence

Useful observations include recorded query/answer/refusal/error counts, signed synthetic checks kept separate, recorded token totals with missing-usage visibility, and voluntary helpfulness clicks. A helpful click is self-reported answer usefulness, not verified business value, task completion, adoption, a testimonial or a unique user. It can be biased, repeated across separate tickets or generated by automation. Do not describe synthetic feedback as visitor feedback. A snapshot containing zero visitor events means no visitor events were observed in that collection window, not that a product has established user outcomes.

Collection is best effort. Writes lost during dependency failure, events outside the instrumented valid-query handler, validation/middleware rejections and requests before `OPS_ENABLED=1` are outside coverage. Missing Redis days mean no retained observations, not proven zero traffic. Histograms permit bucket-level latency inspection; they are not exact latency quantiles or an availability/SLO history. Separate live-provider evaluation, deployment checks and this read-only operations record when presenting evidence.

## Offline verification and recovery

```powershell
python -m unittest discover -s web/tests -p test_observability.py -v
python -m compileall -q web/observability.py operations/collect.py
```

The tests make no network request. They check disabled behavior, secret validation, signed cohort selection, thread-local/nested context restoration, actual/missing token usage, one-write boundaries, no raw ticket/content persistence, collection failure isolation, refusal/error ticket suppression, bounded allowlisted snapshots, optional explicit-rate arithmetic, numeric schema validation, feedback input/result contracts and redirect rejection. Mocked transport tests verify the client contract; they do not establish live Redis Lua atomicity, expiry, deployment readiness or an operated history. Those need explicitly labelled deployed drills.

If Redis becomes unavailable, answers continue when the main answer dependencies remain available; feedback should show that submission is unavailable and protected snapshots return a safe failure. Restore the configured service/credentials and collect a new dated snapshot. Never backfill invented events. Set `OPS_ENABLED=0` to disable collection, rotate a suspected read token and redeploy through the normal configuration workflow, or change `OPS_PREFIX` to isolate an authorized verification run. Existing measurements/tickets expire naturally. There is no destructive cleanup command in this workflow.
