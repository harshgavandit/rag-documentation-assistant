"""Bounded aggregate operations evidence. Never accepts question/answer or identity data."""
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.parse import urlparse

RETENTION_SECONDS = 30 * 86400
TICKET_SECONDS = 3600
CATEGORIES = ("clear", "incomplete", "incorrect", "citation_issue", "other")
BOUNDS_MS = (100, 500, 1000, 3000, 10000, 30000, 60000, 300000)
FIELDS = {"queries", "answered", "refused", "errors", "status_2xx", "status_4xx", "status_5xx",
          "duration_ms_total", "provider_responses", "usage_complete", "usage_missing",
          "prompt_tokens", "output_tokens", "feedback_issued", "feedback_total", "feedback_helpful", "feedback_unhelpful"}
FIELDS.update(f"latency_le_{bound}_ms" for bound in BOUNDS_MS)
FIELDS.update(f"feedback_category_{category}" for category in CATEGORIES)
_event = ContextVar("operations_event", default=None)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None

QUERY_LUA = """
local count = tonumber(redis.call('HGET', KEYS[1], 'queries') or '0')
if count >= 1000000 then return 0 end
for i=1,tonumber(ARGV[1])*2,2 do redis.call('HINCRBY', KEYS[1], ARGV[i+1], ARGV[i+2]) end
redis.call('EXPIRE', KEYS[1], 2592000)
local issued = tonumber(redis.call('HGET', KEYS[1], 'feedback_issued') or '0')
if ARGV[#ARGV] ~= '' and issued < 1000 then
  if not redis.call('SET', KEYS[2], ARGV[#ARGV], 'EX', 3600, 'NX') then return 0 end
  redis.call('HINCRBY', KEYS[1], 'feedback_issued', 1)
  return 1
end
return 0
"""

FEEDBACK_LUA = """
local attempts = redis.call('INCR', KEYS[2])
if attempts == 1 then redis.call('EXPIRE', KEYS[2], 60) end
if attempts > 600 then return -1 end
local scope = redis.call('GET', KEYS[1])
if scope ~= 'visitor' and scope ~= 'synthetic' then return 0 end
local key = KEYS[3]
if scope == 'synthetic' then key = KEYS[4] end
if tonumber(redis.call('HGET', key, 'feedback_total') or '0') >= 10000 then return -1 end
redis.call('DEL', KEYS[1])
redis.call('HINCRBY', key, 'feedback_total', 1)
redis.call('HINCRBY', key, ARGV[1], 1)
redis.call('HINCRBY', key, ARGV[2], 1)
redis.call('EXPIRE', key, 2592000)
return 1
"""


class OperationsUnavailable(Exception):
    """Safe service error without provider details or credentials."""


@dataclass
class Event:
    scope: str
    started: float = field(default_factory=time.monotonic)
    provider_responses: int = 0
    usage_complete: int = 0
    usage_missing: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0
    finished: bool = False
    context_token: object = field(default=None, repr=False)


class Operations:
    """Synchronous best-effort writer and separately protected aggregate reader."""
    def __init__(self, values=None, transport=None):
        self.values = values
        self.transport = transport

    @classmethod
    def from_env(cls):
        return cls()

    def _get(self, name, default=""):
        return str((os.environ if self.values is None else self.values).get(name, default)).strip()

    def is_enabled(self):
        return self._get("OPS_ENABLED") == "1"

    def is_authorized(self, supplied):
        token = self._get("OPS_READ_TOKEN")
        return (len(token) >= 32 and isinstance(supplied, str) and len(supplied) <= 512
                and hmac.compare_digest(token.encode(), supplied.encode()))

    def _prefix(self):
        prefix = self._get("OPS_PREFIX", "docwise:operations:v1")
        if not re.fullmatch(r"[A-Za-z0-9:_-]{1,80}", prefix):
            raise OperationsUnavailable("Operations configuration is unavailable.")
        return prefix

    def _key(self, day, scope):
        return f"{self._prefix()}:daily:{day}:{scope}"

    @staticmethod
    def _day():
        return datetime.now(timezone.utc).date().isoformat()

    def _command(self, payload, pipeline=False):
        if not self.is_enabled():
            raise OperationsUnavailable("Operations collection is disabled.")
        url, token = self._get("UPSTASH_REDIS_REST_URL"), self._get("UPSTASH_REDIS_REST_TOKEN")
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or not token:
            raise OperationsUnavailable("Operations configuration is unavailable.")
        endpoint = url.rstrip("/") + ("/pipeline" if pipeline else "")
        try:
            if self.transport:
                body = self.transport(endpoint, payload, token, 0.75)
            else:
                request = Request(endpoint, data=json.dumps(payload).encode(), headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"}, method="POST")
                with build_opener(_NoRedirect).open(request, timeout=0.75) as response:
                    raw = response.read(65537)
                    if len(raw) > 65536:
                        raise ValueError
                    body = json.loads(raw)
            if pipeline:
                if not isinstance(body, list) or len(body) != len(payload) or any(not isinstance(item, dict) or "error" in item or "result" not in item for item in body):
                    raise ValueError
                return [item["result"] for item in body]
            if not isinstance(body, dict) or "error" in body or "result" not in body:
                raise ValueError
            return body["result"]
        except Exception:
            raise OperationsUnavailable("Operations service is unavailable.") from None

    def begin_request(self, supplied_token=""):
        if not self.is_enabled():
            return None
        event = Event("synthetic" if self.is_authorized(supplied_token) else "visitor")
        event.context_token = _event.set(event)
        return event

    @staticmethod
    def current_event():
        return _event.get()

    @staticmethod
    def record_usage(event, usage):
        if event is None or event.finished:
            return
        event.provider_responses += 1
        # Only use provider-reported integer totals; never estimate from text.
        if not isinstance(usage, dict) or any(type(usage.get(key)) is not int or not 0 <= usage[key] <= 10000000 for key in ("prompt_tokens", "completion_tokens")):
            event.usage_missing += 1
            return
        event.usage_complete += 1
        event.prompt_tokens += usage["prompt_tokens"]
        event.output_tokens += usage["completion_tokens"]

    def finish_request(self, event, status_code=200, outcome="answered"):
        if event is None or event.finished:
            return None
        event.finished = True
        try:
            elapsed = min(300000, max(0, round((time.monotonic() - event.started) * 1000)))
            status = "status_2xx" if 200 <= status_code < 300 else "status_4xx" if 400 <= status_code < 500 else "status_5xx"
            result = outcome if outcome in {"answered", "refused"} and status == "status_2xx" else "errors"
            counters = {"queries": 1, status: 1, result: 1, "duration_ms_total": elapsed,
                        "provider_responses": event.provider_responses, "usage_complete": event.usage_complete,
                        "usage_missing": event.usage_missing, "prompt_tokens": event.prompt_tokens,
                        "output_tokens": event.output_tokens}
            counters.update({f"latency_le_{bound}_ms": int(elapsed <= bound) for bound in BOUNDS_MS})
            ticket = secrets.token_urlsafe(32) if result == "answered" else ""
            ticket_key = self._prefix() + ":ticket:" + hashlib.sha256(ticket.encode()).hexdigest()
            args = [str(len(counters))]
            for key, value in counters.items():
                args.extend((key, str(value)))
            args.append(event.scope if ticket else "")
            issued = self._command(["EVAL", QUERY_LUA, 2, self._key(self._day(), event.scope), ticket_key, *args])
            return ticket if ticket and type(issued) is int and issued == 1 else None
        except Exception:
            return None  # collection must not change the query outcome
        finally:
            if event.context_token is not None:
                try:
                    _event.reset(event.context_token)
                except (ValueError, RuntimeError):
                    _event.set(None)

    def feedback(self, ticket, helpful, category):
        if not isinstance(ticket, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", ticket) or type(helpful) is not bool or category not in CATEGORIES:
            return {"status": "invalid"}
        try:
            prefix, day = self._prefix(), self._day()
            ticket_key = prefix + ":ticket:" + hashlib.sha256(ticket.encode()).hexdigest()
            minute = int(time.time() // 60)
            result = self._command(["EVAL", FEEDBACK_LUA, 4, ticket_key,
                f"{prefix}:feedback-rate:{minute}", self._key(day, "visitor"), self._key(day, "synthetic"),
                "feedback_helpful" if helpful else "feedback_unhelpful", "feedback_category_" + category])
            return {"status": {1: "accepted", 0: "expired_or_used", -1: "limited"}.get(result, "unavailable") if type(result) is int else "unavailable"}
        except Exception:
            return {"status": "unavailable"}

    def snapshot(self, days=7):
        if type(days) is not int or not 1 <= days <= 7:
            raise ValueError("days must be between 1 and 7")
        today = datetime.now(timezone.utc).date()
        dates = [(today - timedelta(days=offset)).isoformat() for offset in range(days)]
        keys = [(day, scope) for day in dates for scope in ("visitor", "synthetic")]
        results = self._command([["HGETALL", self._key(day, scope)] for day, scope in keys], pipeline=True)
        daily = []
        totals = {scope: {field: 0 for field in sorted(FIELDS)} for scope in ("visitor", "synthetic")}
        for (day, scope), raw in zip(keys, results):
            if not isinstance(raw, list) or len(raw) % 2 or len(raw) > len(FIELDS) * 2:
                raise OperationsUnavailable("Operations returned invalid aggregate data.")
            counts = {field: 0 for field in sorted(FIELDS)}
            for index in range(0, len(raw), 2):
                key, value = raw[index:index + 2]
                if key not in FIELDS:
                    continue
                if not isinstance(value, (str, int)) or isinstance(value, bool) or not re.fullmatch(r"\d{1,20}", str(value)):
                    raise OperationsUnavailable("Operations returned invalid aggregate data.")
                counts[key] = int(value)
            for key, value in counts.items():
                totals[scope][key] += value
            daily.append({"date_utc": day, "scope": scope, "counts": counts})
        return {"schema_version": 1, "window_days": days, "retention_days": 30,
                "collected_at_utc": datetime.now(timezone.utc).isoformat(),
                "daily": daily, "totals": totals, "cost": self._cost(totals),
                "definitions": {"visitor": "Requests without a valid operations token; may include bots or unmarked tests, not verified people.",
                    "synthetic": "Requests carrying the valid server operations token; separate from visitor totals.",
                    "feedback": "One-use ticket clicks, self-reported helpfulness; not verified business outcomes or testimonials.",
                    "usage": "Provider-reported prompt/completion tokens on returned responses; failed calls without returned usage cannot establish billed cost.",
                    "latency": "Cumulative histogram in ms; includes request work before telemetry write; values over 300000ms are capped.",
                    "coverage": "Best-effort enabled valid-query handler events only; telemetry loss and pre-handler rejection are not counted. Missing Redis days are zero observations, not proof of zero traffic."}}

    def _cost(self, totals):
        try:
            prompt_rate = Decimal(self._get("OPS_PROMPT_USD_PER_MILLION"))
            output_rate = Decimal(self._get("OPS_OUTPUT_USD_PER_MILLION"))
            if not all(rate.is_finite() and 0 <= rate <= 10000 for rate in (prompt_rate, output_rate)):
                raise ValueError
        except (InvalidOperation, ValueError):
            return {"estimated_usd": None, "note": "No explicit valid prompt/output pricing rates configured; tokens are not converted to dollars."}
        return {"rates_usd_per_million": {"prompt": str(prompt_rate), "output": str(output_rate)},
                "estimated_usd_for_recorded_usage": {scope: str((Decimal(counts["prompt_tokens"]) * prompt_rate + Decimal(counts["output_tokens"]) * output_rate) / 1000000) for scope, counts in totals.items()},
                "note": "Estimate for recorded returned usage only at explicitly configured rates; not provider billing reconciliation, and missing/failed usage may be unobserved."}
