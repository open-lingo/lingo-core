"""Request-time guards for the unauthenticated client-error endpoint.

Implemented as ASGI middleware (`BaseHTTPMiddleware`), not a FastAPI
`Depends`, on purpose: FastAPI reads and JSON-decodes the whole request
body into memory BEFORE it solves any `Depends()` for the route (see
`fastapi.routing.get_request_handler`), so a `Depends`-based size check
runs too late to avoid the parse. Middleware `dispatch()` sees the
`Request` before any of that and can return a `Response` directly without
ever touching the body, which is the only way to reject on `Content-Length`
alone.

Scoped to exactly the telemetry POST paths (`_GUARDED_PATHS`: errors +
diagnostics) so it changes nothing for any other route — this repo has no
general-purpose body-size or rate-limit middleware today, and adding one
for every endpoint is a bigger, separate decision. Both guarded paths
share ONE per-IP token bucket (keyed by IP alone, not by path) — the
one-tap diagnostics button (task A3b #2) is a rare, explicit action, not
worth a second bucket; sharing keeps the "20 burst / ~4 req/min/IP"
budget honest as a single number instead of two to reason about.

What this buys, quantified (see `../lingo/docs/observability-2026-09-17.md`
"Guard math" for the full writeup):

- Body-size cap: `MAX_BODY_BYTES` is sized for the schema's own worst case
  (20 items x (1 KB message + 4 KB stack + ~300 B other fields) ~= 106 KB),
  rounded up. A request over this is rejected on the `Content-Length`
  header alone — no JSON parse, no pydantic validation, no log line.
- Token bucket: `BUCKET_CAPACITY` burst then `REFILL_TOKENS_PER_SEC`
  steady-state, PER SOURCE IP, PER WARM LAMBDA CONTAINER (see docstring
  on `_buckets` below for why "per container" is the honest scope, not
  "per IP globally").
"""

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.shared.request_id import get_request_id

TELEMETRY_ERRORS_PATH = "/api/core/v1/telemetry/errors"
TELEMETRY_DIAGNOSTICS_PATH = "/api/core/v1/telemetry/diagnostics"

# Every path this middleware guards. A path not in this set is untouched —
# `dispatch()` calls `call_next` immediately for it.
_GUARDED_PATHS = frozenset({TELEMETRY_ERRORS_PATH, TELEMETRY_DIAGNOSTICS_PATH})

# ~20 items * (1024 B message + 4096 B stack + ~300 B other fields), rounded
# up with headroom for JSON punctuation/escaping. Reused as-is for
# /diagnostics (task spec: "body cap 150 KB") — that endpoint's own client
# builder already trims its (much bigger, up-to-200-event) session log to
# fit this same budget before POSTing, so one constant serves both.
MAX_BODY_BYTES = 150_000

# Burst of a full batch flush (one device, one bad stretch) is never itself
# throttled; sustained hammering settles to ~4 req/min/IP after the burst.
BUCKET_CAPACITY = 20
REFILL_TOKENS_PER_SEC = 20 / 300  # 1 token / 15s

# Memory guard on the bucket dict itself — see `_bucket_for`.
MAX_TRACKED_IPS = 5_000


class _TokenBucket:
    __slots__ = ("tokens", "last")

    def __init__(self) -> None:
        self.tokens = float(BUCKET_CAPACITY)
        self.last = time.monotonic()

    def allow(self) -> bool:
        now = time.monotonic()
        elapsed = now - self.last
        self.last = now
        self.tokens = min(BUCKET_CAPACITY, self.tokens + elapsed * REFILL_TOKENS_PER_SEC)
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


# Module-level, so it lives for the lifetime of ONE warm Lambda execution
# environment (typically minutes to a couple of hours under steady traffic,
# per AWS's own guidance — not guaranteed, never assume it survives a
# specific request). API Gateway fans out across many concurrent
# environments, each with its OWN dict, so this is real protection against
# one IP hammering one warm container, NOT a global per-IP rate limit
# across the fleet. A determined abuser distributed across N concurrent
# Lambda invocations gets roughly N x BUCKET_CAPACITY burst capacity before
# any single container starts rejecting. Real fleet-wide throttling needs
# an API Gateway usage plan or WAF rate rule (Terraform, lingo-infra,
# outside this repo) — flagged in the observability doc, not built here.
_buckets: dict[str, _TokenBucket] = {}


def _bucket_for(ip: str) -> _TokenBucket:
    if len(_buckets) >= MAX_TRACKED_IPS:
        # Simplest possible eviction. This endpoint's blast radius on a
        # false allow is one extra log line, not money or data, so
        # resetting every tracked IP's bucket under sustained
        # distinct-IP pressure (real traffic growth OR an actual
        # distributed-abuse attempt) is an acceptable trade against
        # unbounded dict growth in a long-lived warm container. A
        # smarter LRU eviction is a fine follow-up, not required to
        # ship this safely.
        _buckets.clear()
    bucket = _buckets.get(ip)
    if bucket is None:
        bucket = _TokenBucket()
        _buckets[ip] = bucket
    return bucket


def reset_telemetry_guard_state() -> None:
    """Test-only: clear bucket state between pytest cases."""
    _buckets.clear()


class TelemetryGuardMiddleware(BaseHTTPMiddleware):
    """Body-size cap + per-IP token bucket, scoped to the telemetry POSTs (_GUARDED_PATHS)."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        if request.method != "POST" or request.url.path not in _GUARDED_PATHS:
            return await call_next(request)

        request_id = get_request_id(request)

        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                size = int(content_length)
            except ValueError:
                size = None
            if size is not None and size > MAX_BODY_BYTES:
                return JSONResponse(
                    {"detail": "client_error payload too large"},
                    status_code=413,
                    headers={"X-Request-Id": request_id},
                )

        ip = request.client.host if request.client else "unknown"
        if not _bucket_for(ip).allow():
            return JSONResponse(
                {"detail": "rate limit exceeded"},
                status_code=429,
                headers={"X-Request-Id": request_id, "Retry-After": "15"},
            )

        return await call_next(request)
