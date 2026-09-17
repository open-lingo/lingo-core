"""POST /api/core/v1/telemetry/errors — the in-house client-error endpoint.

No DB, no auth — this router never touches a repo, so tests hit
`app.main.app` directly with a plain `TestClient` (no lifespan needed,
matching `tests/test_api_error.py`'s style for DB-free routers).

See `../lingo/docs/observability-2026-09-17.md` for the full contract this
pins.
"""

import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.telemetry.guard import MAX_BODY_BYTES, reset_telemetry_guard_state
from app.telemetry.schemas import ClientErrorItem

PATH = "/api/core/v1/telemetry/errors"


@pytest.fixture(autouse=True)
def _clean_guard_state():
    """The token bucket is process-global (by design — see guard.py). Reset
    it around every test so one test's requests never starve another's."""
    reset_telemetry_guard_state()
    yield
    reset_telemetry_guard_state()


def _client() -> TestClient:
    return TestClient(app)


def _item(**overrides: object) -> dict:
    base = {
        "message": "TypeError: cannot read properties of undefined",
        "stack": "at StepRenderer (StepRenderer.tsx:42:9)",
        "source": "window.onerror",
        "route": "/learn/m12",
        "platform": "ios",
        "osVersion": "iOS 18.4",
        "appVersion": "0.0.1",
        "buildNumber": "26",
        "fontScale": 1.25,
        "online": True,
        "count": 1,
        "ts": 1_758_000_000_000,
        "sessionId": "s-abc123",
        "lastRequestId": "req-xyz",
    }
    base.update(overrides)
    return base


# ── Happy path ───────────────────────────────────────────────────────────


def test_accepts_a_batch_and_echoes_count() -> None:
    resp = _client().post(PATH, json={"items": [_item(), _item(message="different error")]})
    assert resp.status_code == 202
    assert resp.json() == {"accepted": 2}


def test_response_carries_x_request_id() -> None:
    resp = _client().post(PATH, json={"items": [_item()]})
    assert resp.status_code == 202
    assert resp.headers.get("X-Request-Id")


def test_minimal_item_only_required_fields() -> None:
    """`stack`, `route`, lesson context, appVersion etc. are all optional —
    a report fired before any of that is available (e.g. the very first
    window.onerror before the lesson engine mounts) must still be valid."""
    resp = _client().post(
        PATH,
        json={"items": [{"message": "boom", "source": "window.onerror", "platform": "web", "ts": 1, "sessionId": "s1"}]},
    )
    assert resp.status_code == 202


# ── Caps: THIS IS THE BEHAVIOR-PINNING TEST ─────────────────────────────────
#
# Guards: `ClientErrorBatch.items` has `max_length=20`
# (app/telemetry/schemas.py). This test fails if that cap is ever loosened
# or removed — verified for real, not just reasoned about: temporarily
# raising `max_length` to 25 in schemas.py and rerunning this test flips it
# from PASS to FAIL (a 21-item batch then returns 202, not 422), which is
# recorded in the lane report rather than only asserted here.


def test_batch_over_20_items_is_rejected_in_full() -> None:
    resp = _client().post(PATH, json={"items": [_item(message=f"error #{i}") for i in range(21)]})
    assert resp.status_code == 422


def test_batch_of_exactly_20_items_is_accepted() -> None:
    resp = _client().post(PATH, json={"items": [_item(message=f"error #{i}") for i in range(20)]})
    assert resp.status_code == 202
    assert resp.json() == {"accepted": 20}


def test_message_over_1kb_is_rejected() -> None:
    resp = _client().post(PATH, json={"items": [_item(message="x" * 1025)]})
    assert resp.status_code == 422


def test_stack_over_4kb_is_rejected() -> None:
    resp = _client().post(PATH, json={"items": [_item(stack="x" * 4097)]})
    assert resp.status_code == 422


def test_empty_batch_is_rejected() -> None:
    resp = _client().post(PATH, json={"items": []})
    assert resp.status_code == 422


def test_oversized_raw_body_is_rejected_with_413_before_parsing() -> None:
    """Guards `TelemetryGuardMiddleware`: a body over MAX_BODY_BYTES is
    rejected on the Content-Length header alone, before FastAPI/pydantic
    ever look at it — so this sends bytes that aren't even valid JSON and
    still expects a clean 413, not a 422 from a failed parse."""
    oversized = b"x" * (MAX_BODY_BYTES + 1)
    resp = _client().post(PATH, content=oversized, headers={"content-type": "application/json"})
    assert resp.status_code == 413


def test_body_under_cap_is_not_rejected_by_size_guard() -> None:
    # Sanity check on the boundary itself, using a real (parseable) payload
    # just under the byte cap AND under every per-field cap, so a
    # false-positive size-guard rejection can't hide behind the
    # deliberately-invalid-JSON test above, nor be confused with a 422
    # from a field being too long.
    items = [_item(message=f"error #{i}", stack="x" * 3000) for i in range(20)]
    body = json.dumps({"items": items}).encode()
    assert len(body) < MAX_BODY_BYTES  # sanity on the fixture itself
    resp = _client().post(PATH, content=body, headers={"content-type": "application/json"})
    assert resp.status_code == 202
    assert resp.json() == {"accepted": 20}


# ── Rate limit ───────────────────────────────────────────────────────────
#
# Guards: `TelemetryGuardMiddleware`'s per-IP token bucket
# (BUCKET_CAPACITY=20, refill 1 token/15s). Starlette's TestClient presents
# every request from the same synthetic client address, so this exercises
# exactly the single-IP path.


def test_requests_beyond_bucket_capacity_get_429() -> None:
    from app.telemetry.guard import BUCKET_CAPACITY

    client = _client()
    statuses = [client.post(PATH, json={"items": [_item()]}).status_code for _ in range(BUCKET_CAPACITY + 5)]
    assert statuses[:BUCKET_CAPACITY] == [202] * BUCKET_CAPACITY
    assert 429 in statuses[BUCKET_CAPACITY:]


def test_429_response_carries_retry_after_and_request_id() -> None:
    from app.telemetry.guard import BUCKET_CAPACITY

    client = _client()
    for _ in range(BUCKET_CAPACITY):
        client.post(PATH, json={"items": [_item()]})
    resp = client.post(PATH, json={"items": [_item()]})
    assert resp.status_code == 429
    assert resp.headers.get("Retry-After")
    assert resp.headers.get("X-Request-Id")


# ── Structured log line ──────────────────────────────────────────────────
#
# Guards: `report_client_errors` logs exactly one `lingo.client_error`
# WARNING per item, as a single JSON line carrying every field CloudWatch
# Insights needs (see the doc's Insights query). Flipped for real: renaming
# the `"type"` key (or logging at INFO instead of WARNING) makes this fail —
# recorded in the lane report.


def test_logs_one_structured_warning_line_per_item(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="lingo.client_error")
    resp = _client().post(
        PATH,
        json={"items": [_item(message="first error"), _item(message="second error", count=3)]},
    )
    request_id = resp.headers["X-Request-Id"]

    records = [r for r in caplog.records if r.name == "lingo.client_error"]
    assert len(records) == 2
    for r in records:
        assert r.levelno == logging.WARNING

    payloads = [json.loads(r.getMessage()) for r in records]
    assert payloads[0]["type"] == "client_error"
    assert payloads[0]["message"] == "first error"
    assert payloads[0]["requestId"] == request_id
    assert payloads[1]["message"] == "second error"
    assert payloads[1]["count"] == 3
    # Every field the Insights query in the observability doc groups/filters
    # by must round-trip.
    for key in ("source", "platform", "route", "sessionId", "lastRequestId"):
        assert key in payloads[0]


# ── No PII in the schema ─────────────────────────────────────────────────
#
# Guards: nobody adds a user id / email / name / free-text-answer field to
# `ClientErrorItem` later without noticing. This is a schema-shape
# assertion, not a runtime one — the privacy contract is "these fields
# cannot exist to be sent", not "we choose not to populate them".


def test_schema_has_no_pii_fields() -> None:
    banned_substrings = ("email", "userid", "user_id", "username", "password", "answer", "freetext", "displayname")
    field_names = {name.lower() for name in ClientErrorItem.model_fields}
    for banned in banned_substrings:
        assert not any(banned in name for name in field_names), f"PII-shaped field name matched {banned!r}: {field_names}"


# ── X-Request-Id on error responses ──────────────────────────────────────


def test_validation_error_response_carries_x_request_id() -> None:
    resp = _client().post(PATH, json={"items": "not-a-list"})
    assert resp.status_code == 422
    assert resp.headers.get("X-Request-Id")


def test_404_response_carries_x_request_id() -> None:
    resp = _client().get("/api/core/v1/telemetry/does-not-exist")
    assert resp.status_code == 404
    assert resp.headers.get("X-Request-Id")


# ── Surface mode ──────────────────────────────────────────────────────────


def test_telemetry_mounted_in_both_full_and_beta_surface_modes() -> None:
    from fastapi import FastAPI

    from app.v1.router import build_v1_router

    for mode in ("full", "beta"):
        probe = FastAPI()
        probe.include_router(build_v1_router(mode))
        paths = probe.openapi()["paths"].keys()
        assert any(p.startswith("/telemetry") for p in paths), f"telemetry not mounted in {mode} mode"
