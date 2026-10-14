"""Shared fixed-window admission control using an atomic Upstash Redis Lua script."""
import hashlib
import json
import math
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from config import settings

# The window starts at the first admitted request. Rejected requests never extend it.
RATE_LIMIT_SCRIPT = """
local limit = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local count = tonumber(redis.call('GET', KEYS[1]) or '0')
local ttl = redis.call('PTTL', KEYS[1])
if count >= limit and ttl > 0 then return {0, 0, ttl} end
if ttl <= 0 then
  redis.call('SET', KEYS[1], 1, 'PX', window)
  return {1, limit - 1, window}
end
count = redis.call('INCR', KEYS[1])
return {1, limit - count, ttl}
"""


class RateLimitExceeded(RuntimeError):
    """A query was denied before retrieval or a model call."""

    def __init__(self, retry_after_seconds: int) -> None:
        """Expose a useful retry delay to CLI or future API callers."""
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Rate limit exceeded. Retry after {retry_after_seconds} seconds.")


def redis_command(command: list) -> object:
    """Execute one REST command, failing closed and keeping credentials out of errors."""
    request = Request(
        settings.UPSTASH_REDIS_REST_URL,
        data=json.dumps(command).encode("utf-8"),
        headers={"Authorization": "Bearer " + settings.UPSTASH_REDIS_REST_TOKEN,
                 "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=settings.REDIS_TIMEOUT_SECONDS) as response:
            payload = json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"Redis request failed (HTTP {exc.code}); query blocked.") from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise RuntimeError("Redis unavailable or invalid response; query blocked.") from None
    if not isinstance(payload, dict) or "error" in payload or "result" not in payload:
        raise RuntimeError("Redis rejected the command or returned an invalid response; query blocked.")
    return payload["result"]


def enforce_rate_limit() -> dict:
    """Limit all query callers sharing this application namespace, across processes."""
    scope = hashlib.sha256((settings.COLLECTION_NAME + ":" + settings.OPENAI_MODEL).encode()).hexdigest()[:24]
    result = redis_command([
        "EVAL", RATE_LIMIT_SCRIPT, 1, f"{settings.RATE_LIMIT_PREFIX}:{scope}",
        settings.RATE_LIMIT_REQUESTS, settings.RATE_LIMIT_WINDOW_SECONDS * 1000,
    ])
    if (not isinstance(result, list) or len(result) != 3
            or any(type(value) is not int for value in result)
            or result[0] not in (0, 1) or result[1] < 0 or result[2] <= 0):
        raise RuntimeError("Redis returned an invalid rate-limit result; query blocked.")
    allowed, remaining, ttl_ms = result
    retry_after = max(1, math.ceil(ttl_ms / 1000))
    if not allowed:
        raise RateLimitExceeded(retry_after)
    return {"remaining": remaining, "reset_after_seconds": retry_after}
