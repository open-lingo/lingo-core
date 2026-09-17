"""Pydantic schemas for the unauthenticated client-error endpoint.

See `../lingo/docs/observability-2026-09-17.md` for the full contract
(payload shape, CloudWatch query, alarm, privacy statement) and
`app/telemetry/router.py` for how these are logged.

Every string field is hard-capped so a single oversized item cannot blow
up CloudWatch ingest cost or memory — pydantic rejects the WHOLE batch
with a 422 if any item is over cap (mirrors `progress/schemas.py`'s
`BatchAttempt` — no partial success, the client must not need per-item
results to know what to retry).
"""

from pydantic import BaseModel, Field


class ClientErrorItem(BaseModel):
    """One deduplicated client-side error report.

    Deliberately NOT a `Literal` on `source`/`platform` — the client and
    server ship independently (content-in-binary, see CLAUDE.md), so a
    client ahead of the server's known `source` values must not have its
    reports rejected outright. Treat these as free text, capped, logged
    verbatim; CloudWatch Insights `stats count() by source` still works
    on unknown values.
    """

    message: str = Field(min_length=1, max_length=1024, description="Error message, truncated client-side")
    stack: str | None = Field(default=None, max_length=4096, description="Stack trace, first 4 KB")
    source: str = Field(
        min_length=1,
        max_length=64,
        description="window.onerror | unhandledrejection | AppErrorBoundary | RouteErrorBoundary | chunk-load | boot-guard",
    )
    route: str | None = Field(default=None, max_length=256, description="Path only, no query string")
    lessonId: str | None = Field(default=None, max_length=128)
    stepIndex: int | None = Field(default=None, ge=0, le=10_000)
    stepType: str | None = Field(default=None, max_length=64)
    appVersion: str | None = Field(default=None, max_length=64)
    buildNumber: str | None = Field(default=None, max_length=32)
    platform: str = Field(min_length=1, max_length=16, description="ios | android | web")
    osVersion: str | None = Field(default=None, max_length=64)
    fontScale: float | None = Field(default=None, ge=0.1, le=5.0, description="App-owned accessibility font multiplier, e.g. 1.25")
    online: bool | None = Field(default=None, description="navigator.onLine at report time")
    count: int = Field(default=1, ge=1, le=100_000, description="Occurrences of this message+stack this session (dedupe)")
    ts: int = Field(ge=0, description="Client epoch ms when first seen")
    sessionId: str = Field(min_length=1, max_length=64, description="Random per-session id, NOT a user id")
    lastRequestId: str | None = Field(default=None, max_length=64, description="X-Request-Id from the most recent API response, for grepping server logs")


class ClientErrorBatch(BaseModel):
    """Body of POST /api/core/v1/telemetry/errors.

    `max_length=20` mirrors the client's per-session report cap
    (`errorReporter.ts`) and is also a hard ceiling on any single
    request regardless of what a client claims — a batch over this size
    422s in full, same all-or-nothing contract as progress batching.
    """

    items: list[ClientErrorItem] = Field(min_length=1, max_length=20)


class ClientErrorAcceptedResponse(BaseModel):
    accepted: int
