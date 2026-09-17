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

import json

from pydantic import BaseModel, Field, field_validator

# Mirrored client-side in `../lingo/src/shared/telemetry/errorReporter.ts`
# (`MAX_BREADCRUMBS` / `MAX_BREADCRUMBS_BYTES`) — the client already trims to
# these caps before sending, so a batch that trips them server-side means a
# client is either out of sync with this contract or sending a hand-built
# payload; reject rather than silently truncate (same all-or-nothing 422
# contract as every other cap in this file).
MAX_BREADCRUMBS = 20
MAX_BREADCRUMBS_BYTES = 4096


class ClientErrorBreadcrumb(BaseModel):
    """One `sessionLog.ts` event, carried on an error report for context —
    "what did the learner do in the ~20 events before this broke." Built
    client-side by `errorReporter.ts::buildBreadcrumbs`.

    No PII by construction: `payload` values are whatever `sessionLog.ts`
    event payloads already contain (lesson content — atom labels, step
    ids/types, counts — never user-typed free text; see that module's own
    "No PII" docstring), and are pre-trimmed client-side to <=120 chars each
    before this model ever sees them (still re-capped here at the STRING
    level as defense in depth — see `payload`'s Field).
    """

    t: int = Field(description="ms relative to the report's own `ts`, typically <= 0 (before or at the error)")
    type: str = Field(min_length=1, max_length=64, description="sessionLog.ts SessionEventType, e.g. 'step_view'")
    payload: dict[str, str] | None = Field(
        default=None,
        description="Session-log event payload, values pre-trimmed client-side to <=120 chars",
    )

    @field_validator("payload")
    @classmethod
    def _cap_payload_values(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        if v is None:
            return v
        # Defense in depth: re-truncate server-side too, rather than reject,
        # since a client a few bytes over its own trim (unicode width
        # differences, a client version skew) shouldn't 422 an otherwise-
        # valid error report over one long breadcrumb value.
        return {k: (val if len(val) <= 120 else val[:120]) for k, val in v.items()}


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
    breadcrumbs: list[ClientErrorBreadcrumb] | None = Field(
        default=None,
        max_length=MAX_BREADCRUMBS,
        description="Last <=20 sessionLog.ts events before this report, oldest first",
    )

    @field_validator("breadcrumbs")
    @classmethod
    def _cap_breadcrumbs_total_bytes(cls, v: list[ClientErrorBreadcrumb] | None) -> list[ClientErrorBreadcrumb] | None:
        if not v:
            return v
        size = len(json.dumps([b.model_dump() for b in v]).encode("utf-8"))
        if size > MAX_BREADCRUMBS_BYTES:
            raise ValueError(f"breadcrumbs exceed {MAX_BREADCRUMBS_BYTES} bytes ({size} bytes)")
        return v


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


# ── Diagnostics document (one-tap "Send diagnostics") ───────────────────────
#
# Bigger and rarer than a ClientErrorItem: fired once, on demand, by a
# tester tapping a button in the Sync panel — not automatically on every
# error. Body-size guard (`guard.py`'s TelemetryGuardMiddleware, same
# 150 KB cap as /errors) is this endpoint's primary protection rather than
# tight per-field caps; see `ClientDiagnosticsDocument`'s docstring.

MAX_DIAGNOSTICS_SESSION_EVENTS = 200


class DiagnosticsSessionEvent(BaseModel):
    """One `sessionLog.ts` event, full fidelity (NOT breadcrumb-trimmed —
    see `ClientErrorBreadcrumb` for the 20-event/120-char-value version
    that rides along on every error report). `payload` is deliberately
    untyped: `sessionLog.ts` payloads vary by event type (lesson ids, step
    indices, tile labels, counts — never user-typed free text, per that
    module's own "No PII" docstring) and this endpoint is a rare, explicit,
    one-tap dump, not a per-error automatic send.
    """

    ts: int = Field(ge=0, description="Client epoch ms")
    type: str = Field(min_length=1, max_length=64, description="sessionLog.ts SessionEventType")
    payload: dict[str, object] | None = None


class DiagnosticsDeviceInfo(BaseModel):
    platform: str = Field(min_length=1, max_length=16, description="ios | android | web")
    osVersion: str | None = Field(default=None, max_length=64)
    appVersion: str | None = Field(default=None, max_length=64)
    buildNumber: str | None = Field(default=None, max_length=32)
    fontScale: float | None = Field(default=None, ge=0.1, le=5.0)
    viewport: str | None = Field(default=None, max_length=32, description="'WxH' CSS px")


class ClientDiagnosticsDocument(BaseModel):
    """Body of POST /api/core/v1/telemetry/diagnostics — the one-tap "Send
    diagnostics" button next to the Layout-trace tools in the mobile Sync
    panel (`lingo/src/features/sync/LayoutTracePanel.tsx`). Unauthenticated
    for the same reason `/telemetry/errors` is (see that router's
    docstring) — a tester hits this from wherever the app is failing,
    which may be before login.

    `layoutTrace` / `tapReplay` are opaque dicts, not typed against
    `lingo/src/shared/dev/layoutTrace.ts` / `sessionLog.ts::TapReplayDoc` —
    those shapes are owned by other lanes/files and already internally
    bounded client-side (a layout trace is <=700ms of frames; a tap
    replay is one step's taps), and this endpoint's 150 KB body-size guard
    (`guard.py`) is the real backstop regardless of their exact shape.
    """

    sessionLog: list[DiagnosticsSessionEvent] = Field(default_factory=list, max_length=MAX_DIAGNOSTICS_SESSION_EVENTS)
    layoutTrace: dict[str, object] | None = None
    tapReplay: dict[str, object] | None = None
    device: DiagnosticsDeviceInfo
    lastRequestId: str | None = Field(default=None, max_length=64)


class ClientDiagnosticsAcceptedResponse(BaseModel):
    code: str = Field(min_length=6, max_length=6, description="Unambiguous 6-char code (no 0/O/1/I) — read back via scripts/ops/pull-diagnostics.mjs")
