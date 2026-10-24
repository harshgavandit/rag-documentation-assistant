# Hosted workload diagnostic

`hosted_workload.py` measures the deployed Vercel FastAPI, Redis admission, ONNX retrieval, answer guards and usage-accounting path on an isolated **preview**. The provider is deterministic and cannot contact OpenAI. These timings do not measure model latency, sustained service capacity, real visitor outcomes or an availability SLO.

The production app is unchanged unless staging is explicitly configured. An enabled or malformed staging flag on a production deployment fails closed. Preview queries require two distinct private tokens and a fixed scenario allowlist before admission. The adapter requires the dummy key `staging-unused-no-provider`, preview environment, exact committed source SHA, isolated namespace nonce, fixed request limits and fixture pricing. Protected provenance checks the deployed source/model hashes, Vercel deployment identity and installed runtime versions. No query can select arbitrary endpoints, code, model or delays.

Deploy the committed `web` source to the existing project's preview environment with per-deployment variables documented in `web/staging.py`. Do not change production project variables or promote a staging alias. Use fresh admission/operations prefixes and private tokens. The existing Redis service is reused only under those isolated prefixes; normal production budgets and records are untouched. Fixture prices are arithmetic test inputs, not provider pricing or billed cost.

```powershell
python operations/hosted_workload.py --live --base-url https://<immutable-preview-host>.vercel.app --control-file /private/staging-controls.env --expected-source-sha <full-commit> --expected-run-id <nonce> --output operations/results/hosted-staging-new.json
```

The control file contains only `STAGING_WORKLOAD_TOKEN`, `OPS_READ_TOKEN`, and, when necessary, the existing `VERCEL_AUTOMATION_BYPASS_SECRET`. None are printed or recorded. The probe refuses mutable aliases, mismatched provenance, dirty/uncommitted runtime/probe sources, pre-used namespaces and existing output files. The normal run makes four auxiliary reads and at most 16 query attempts, with a concurrency ceiling of eight. It persists every status, individual wall duration, relevant admission TTL, safe citation/result checks and aggregate deltas. It waits for real Redis window expiry, with no counter reset or retry expansion.

The four phases cover complete answers, rejected citations, provider failure, missing usage, truncated output, refusal, concurrent admission and daily-budget denial. Expected accounting is six `2xx`, three `5xx`, seven `4xx`, seven complete fixture usage records and one missing usage record. The fixture estimate is `0.00098`; actual provider calls and provider spend are zero. Offline protocol tests simulate the same phases with independent state and injected sleep; they are separate from a real hosted run.

After verification, remove only the disposable preview deployment. Let its exact Redis keys expire naturally. Keep the report, source hashes and deployment identity; do not call injected fixtures customer traffic or actual AI inference. On failure, preserve the failed report and investigate before authorizing another bounded run.

Production rollback uses the ordinary Vercel deployment history and a previously verified source release. Check public health and authenticated operations schema after rollback. Do not enable staging to work around a production dependency failure. Restore missing provider/Redis configuration, keep failed observations, and record an actual incident only if it occurred. Existing daily health/schema monitoring accumulates future observations; scheduled configuration does not create a past operating history.
