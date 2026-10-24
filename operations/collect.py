"""Read-only health and authenticated aggregate snapshot; never invokes a model."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from time import perf_counter
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen, build_opener, HTTPRedirectHandler

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))
from observability import FIELDS


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None  # never forward a read token to a different endpoint


def fetch(url, token=None):
    started = perf_counter()
    headers = {"Accept": "application/json"}
    if token:
        headers["x-ops-token"] = token
    request = Request(url, headers=headers, method="GET")
    try:
        with build_opener(NoRedirect).open(request, timeout=10) as response:
            raw = response.read(131073)
            if len(raw) > 131072:
                return {"http_status": response.status, "error": "response_too_large"}, None
            body = json.loads(raw)
            return {"http_status": response.status, "elapsed_ms": round((perf_counter()-started)*1000, 3)}, body
    except HTTPError as error:
        return {"http_status": error.code, "error": "endpoint_unavailable", "elapsed_ms": round((perf_counter()-started)*1000, 3)}, None
    except Exception:
        return {"http_status": None, "error": "request_failed", "elapsed_ms": round((perf_counter()-started)*1000, 3)}, None


def safe_snapshot(body):
    """Rebuild an allowlisted artifact; discard all server-provided free text."""
    if not isinstance(body, dict) or body.get("schema_version") != 1 or type(body.get("window_days")) is not int or not 1 <= body["window_days"] <= 7:
        raise ValueError
    clean = {"schema_version": 1, "window_days": body["window_days"], "retention_days": 30, "daily": [], "totals": {}}
    def counts(values):
        if not isinstance(values, dict):
            raise ValueError
        result = {}
        for field in sorted(FIELDS):
            value = values.get(field, 0)
            if type(value) is not int or not 0 <= value <= 10 ** 22:
                raise ValueError
            result[field] = value
        return result
    rows = body.get("daily")
    if not isinstance(rows, list) or len(rows) > 14:
        raise ValueError
    for row in rows:
        if not isinstance(row, dict) or row.get("scope") not in {"visitor", "synthetic"} or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(row.get("date_utc", ""))):
            raise ValueError
        clean["daily"].append({"date_utc": row["date_utc"], "scope": row["scope"], "counts": counts(row.get("counts"))})
    totals = body.get("totals")
    if not isinstance(totals, dict):
        raise ValueError
    for scope in ("visitor", "synthetic"):
        clean["totals"][scope] = counts(totals.get(scope))
    # Costs are not recomputed from an arbitrary model price. Persist only numeric
    # estimates and explicit rates if the server reports its configured pair.
    clean["cost"] = {"estimated_usd": None}
    cost = body.get("cost", {})
    if isinstance(cost, dict) and isinstance(cost.get("rates_usd_per_million"), dict) and isinstance(cost.get("estimated_usd_for_recorded_usage"), dict):
        numbers = [cost["rates_usd_per_million"].get(key) for key in ("prompt", "output")]
        numbers += [cost["estimated_usd_for_recorded_usage"].get(key) for key in ("visitor", "synthetic")]
        if all(isinstance(value, str) and re.fullmatch(r"\d{1,20}(?:\.\d{1,20})?", value) for value in numbers):
            clean["cost"] = {"rates_usd_per_million": dict(zip(("prompt", "output"), numbers[:2])),
                             "estimated_usd_for_recorded_usage": dict(zip(("visitor", "synthetic"), numbers[2:]))}
    return clean


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("OPS_BASE_URL", ""))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--public-health-only", action="store_true",
                        help="validate protected aggregates but save only health, schema validity and failure states")
    args = parser.parse_args(argv)
    base = args.base_url.rstrip("/")
    parsed = urlparse(base)
    token = os.getenv("OPS_READ_TOKEN", "").strip()
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
        parser.error("base URL must be an HTTPS origin without credentials, path or query")
    if not 32 <= len(token) <= 512:
        parser.error("OPS_READ_TOKEN must contain 32..512 characters")
    if args.output.suffix != ".json" or args.output.exists() or not args.output.parent.is_dir():
        parser.error("output must be a new .json file in an existing directory")
    report = {"schema_version": 1, "collected_at_utc": datetime.now(timezone.utc).isoformat(),
              "target_host": parsed.hostname,
              "scope": "Read-only public health and authenticated seven-day aggregates; zero queries or paid-provider calls.",
              "limitations": "Visitor is an unsigned-request cohort, not verified people. Feedback clicks are self-reported usefulness, not business outcomes. Best-effort telemetry can miss events; missing days do not establish zero traffic."}
    if args.public_health_only:
        report["scope"] = "Read-only health and protected aggregate schema verification; private measurements omitted."
        report["limitations"] = "This public artifact contains health and schema check outcomes only; it establishes no traffic, usage or user outcomes."
    health, health_body = fetch(base + "/api/health")
    health["ready"] = isinstance(health_body, dict) and health_body.get("status") == "ready" and health["http_status"] == 200
    report["health"] = health
    snapshot, body = fetch(base + "/api/operations?days=7", token)
    report["aggregate_schema_valid"] = False
    if snapshot["http_status"] == 200:
        try:
            aggregates = safe_snapshot(body)
            report["aggregate_schema_valid"] = True
            if not args.public_health_only:
                report["aggregates"] = aggregates
        except (ValueError, TypeError):
            snapshot["error"] = "invalid_aggregate_schema"
    report["operations"] = snapshot
    report["passed"] = health["ready"] and report["aggregate_schema_valid"]
    with args.output.open("x", encoding="utf-8") as output:
        output.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(f"Wrote {args.output.name}; passed={report['passed']}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
