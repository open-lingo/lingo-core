"""POST /api/core/v1/telemetry/diagnostics — the one-tap "Send diagnostics"
button in the mobile Sync panel (A3b, 2026-09-17).

Same DB-free, plain-`TestClient` style as `test_telemetry_errors.py`. See
`../lingo/docs/observability-2026-09-17.md` for the full contract and
`app/telemetry/router.py`/`app/telemetry/guard.py` for the implementation
this pins.
"""

import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.telemetry.guard import BUCKET_CAPACITY, MAX_BODY_BYTES, reset_telemetry_guard_state
from app.telemetry.router import DIAGNOSTICS_CODE_ALPHABET, DIAGNOSTICS_CODE_LENGTH

PATH = "/api/core/v1/telemetry/diagnostics"


@pytest.fixture(autouse=True)
def _clean_guard_state():
    """The token bucket is process-global and (by design, A3b) SHARED
    between /errors and /diagnostics — reset it around every test so one
    test's requests never starve another's."""
    reset_telemetry_guard_state()
    yield
    reset_telemetry_guard_state()


def _client() -> TestClient:
    return TestClient(app)


def _doc(**overrides: object) -> dict:
    base = {
        "sessionLog": [
            {"ts": 1_758_000_000_000, "type": "lesson_start", "payload": {"lessonId": "m12"}},
            {"ts": 1_758_000_001_000, "type": "step_view", "payload": {"stepIndex": 3}},
        ],
        "layoutTrace": {"frames": 12, "changed": []},
        "tapReplay": {"route": "/learn/m12", "taps": []},
        "device": {
            "platform": "ios",
            "osVersion": "iOS 18.4",
            "appVersion": "0.0.1",
            "buildNumber": "26",
            "fontScale": 1.25,
            "viewport": "430x932",
        },
        "lastRequestId": "req-xyz",
    }
    base.update(overrides)
    return base


# ── Happy path ───────────────────────────────────────────────────────────


def test_accepts_a_document_and_returns_a_code() -> None:
    resp = _client().post(PATH, json=_doc())
    assert resp.status_code == 202
    body = resp.json()
    assert set(body.keys()) == {"code"}
    assert len(body["code"]) == DIAGNOSTICS_CODE_LENGTH


def test_code_uses_only_the_unambiguous_alphabet() -> None:
    resp = _client().post(PATH, json=_doc())
    code = resp.json()["code"]
    assert all(ch in DIAGNOSTICS_CODE_ALPHABET for ch in code)
    for banned in "0O1I":
        assert banned not in code


def test_codes_differ_across_calls() -> None:
    client = _client()
    codes = {client.post(PATH, json=_doc()).json()["code"] for _ in range(10)}
    # Not a strict uniqueness guarantee (birthday collisions are possible),
    # but 10 calls colliding would be a near-impossible fluke given
    # len(alphabet)**6 ~= 1.07e9 possibilities — a real bug (e.g. a
    # hardcoded seed) would fail this every time, not flakily.
    assert len(codes) == 10


def test_response_carries_x_request_id() -> None:
    resp = _client().post(PATH, json=_doc())
    assert resp.status_code == 202
    assert resp.headers.get("X-Request-Id")


def test_minimal_document_only_device_required() -> None:
    """`sessionLog`/`layoutTrace`/`tapReplay`/`lastRequestId` are all
    optional-or-empty — a diagnostics tap on a fresh session with no trace
    and no taps yet must still be valid."""
    resp = _client().post(PATH, json={"device": {"platform": "web"}})
    assert resp.status_code == 202


# ── Caps ─────────────────────────────────────────────────────────────────


def test_session_log_over_200_events_is_rejected() -> None:
    events = [{"ts": i, "type": "step_view", "payload": {}} for i in range(201)]
    resp = _client().post(PATH, json=_doc(sessionLog=events))
    assert resp.status_code == 422


def test_session_log_of_exactly_200_events_is_accepted() -> None:
    events = [{"ts": i, "type": "step_view", "payload": {}} for i in range(200)]
    resp = _client().post(PATH, json=_doc(sessionLog=events))
    assert resp.status_code == 202


def test_oversized_raw_body_is_rejected_with_413_before_parsing() -> None:
    """Guards `TelemetryGuardMiddleware` now also covering this path (task
    spec: "guarded by the same TelemetryGuardMiddleware; body cap 150 KB")
    — bytes over MAX_BODY_BYTES are rejected on Content-Length alone,
    before any JSON parse, same as /errors."""
    oversized = b"x" * (MAX_BODY_BYTES + 1)
    resp = _client().post(PATH, content=oversized, headers={"content-type": "application/json"})
    assert resp.status_code == 413


def test_body_under_cap_is_not_rejected_by_size_guard() -> None:
    events = [{"ts": i, "type": "step_view", "payload": {"note": "x" * 200}} for i in range(200)]
    body = json.dumps(_doc(sessionLog=events)).encode()
    assert len(body) < MAX_BODY_BYTES
    resp = _client().post(PATH, content=body, headers={"content-type": "application/json"})
    assert resp.status_code == 202


# ── Rate limit — SHARED bucket with /errors (A3b design: "the existing
# per-IP bucket") ─────────────────────────────────────────────────────────


def test_diagnostics_and_errors_share_one_per_ip_bucket() -> None:
    from app.telemetry.schemas import ClientErrorItem  # noqa: F401  (import kept local: mirrors test_telemetry_errors.py style)

    errors_path = "/api/core/v1/telemetry/errors"
    error_item = {
        "message": "boom",
        "source": "window.onerror",
        "platform": "web",
        "ts": 1,
        "sessionId": "s1",
    }
    client = _client()
    # Spend the whole burst on /errors...
    statuses = [client.post(errors_path, json={"items": [error_item]}).status_code for _ in range(BUCKET_CAPACITY)]
    assert statuses == [202] * BUCKET_CAPACITY
    # ...then /diagnostics from the SAME IP is immediately rate-limited too,
    # proving the bucket is shared rather than per-path.
    resp = client.post(PATH, json=_doc())
    assert resp.status_code == 429


def test_requests_beyond_bucket_capacity_get_429() -> None:
    client = _client()
    statuses = [client.post(PATH, json=_doc()).status_code for _ in range(BUCKET_CAPACITY + 5)]
    assert statuses[:BUCKET_CAPACITY] == [202] * BUCKET_CAPACITY
    assert 429 in statuses[BUCKET_CAPACITY:]


# ── Structured log line ──────────────────────────────────────────────────


def test_logs_one_structured_warning_line_keyed_by_the_code(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="lingo.client_diag")
    resp = _client().post(PATH, json=_doc())
    code = resp.json()["code"]
    request_id = resp.headers["X-Request-Id"]

    records = [r for r in caplog.records if r.name == "lingo.client_diag"]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING

    payload = json.loads(records[0].getMessage())
    assert payload["type"] == "client_diag"
    assert payload["code"] == code
    assert payload["requestId"] == request_id
    assert payload["device"]["platform"] == "ios"
    assert payload["lastRequestId"] == "req-xyz"
    assert len(payload["sessionLog"]) == 2


def test_diagnostics_never_logs_on_the_client_error_logger(caplog: pytest.LogCaptureFixture) -> None:
    """Guards the two endpoints staying on separate logger names — a
    Logs Insights query for one must never silently pick up the other."""
    caplog.set_level(logging.WARNING)
    _client().post(PATH, json=_doc())
    assert not [r for r in caplog.records if r.name == "lingo.client_error"]


# ── Surface mode ──────────────────────────────────────────────────────────


def test_diagnostics_mounted_in_both_full_and_beta_surface_modes() -> None:
    from fastapi import FastAPI

    from app.v1.router import build_v1_router

    for mode in ("full", "beta"):
        probe = FastAPI()
        probe.include_router(build_v1_router(mode))
        paths = probe.openapi()["paths"].keys()
        assert any(p.endswith("/telemetry/diagnostics") for p in paths), f"diagnostics not mounted in {mode} mode"
