"""POST /api/core/v1/telemetry/outcomes — per-word difficulty stats (T7).

Authenticated, unlike `/errors`/`/diagnostics` (see `AtomOutcomeItem`'s
docstring in `app/telemetry/schemas.py` for why): the server hashes the
caller's `sub` into the log line, so this uses the `api_client` fixture
(DB-backed registered dev user) rather than a bare `TestClient` the way
`test_telemetry_errors.py` does.

See `../lingo/docs/atom-outcome-telemetry-2026-09-18.md` for the full
contract this pins.
"""

import json
import logging

import pytest

PATH = "/api/core/v1/telemetry/outcomes"


def _item(**overrides: object) -> dict:
    base = {
        "lang": "ja",
        "lessonId": "ja-m12-neo-3",
        "stepIndex": 4,
        "stepType": "build_sentence",
        "atomIds": ["ja:vocab:taberu"],
        "correct": True,
        "msToAnswer": 2400,
        "attempt": 1,
        "srcSurface": "lesson",
        "buildNumber": "29",
    }
    base.update(overrides)
    return base


# ── Happy path ───────────────────────────────────────────────────────────


def test_accepts_a_batch_and_echoes_count(api_client) -> None:
    client, _user_id, _admin_id = api_client
    resp = client.post(PATH, json={"items": [_item(), _item(stepIndex=5, correct=False)]})
    assert resp.status_code == 202
    assert resp.json() == {"accepted": 2}


def test_response_carries_x_request_id(api_client) -> None:
    client, _user_id, _admin_id = api_client
    resp = client.post(PATH, json={"items": [_item()]})
    assert resp.status_code == 202
    assert resp.headers.get("X-Request-Id")


def test_minimal_item_only_required_fields(api_client) -> None:
    """`atomIds`, `buildNumber`, `attempt` are all optional/defaulted —
    a step with no exercised atoms (shouldn't normally happen, but the
    schema must not crash on it) still validates."""
    client, _user_id, _admin_id = api_client
    resp = client.post(
        PATH,
        json={
            "items": [
                {
                    "lang": "es",
                    "lessonId": "es-m3-2",
                    "stepIndex": 0,
                    "stepType": "multiple_choice",
                    "correct": False,
                    "msToAnswer": 1200,
                    "srcSurface": "lesson",
                }
            ]
        },
    )
    assert resp.status_code == 202


def test_unauthenticated_request_is_rejected(api_client, monkeypatch) -> None:
    """With DEBUG off and no JWT, the route should 401 — this endpoint is
    NOT the same "before login" shape as /errors and /diagnostics (see
    `AtomOutcomeItem`'s docstring for why it must be authenticated)."""
    client, _user_id, _admin_id = api_client

    from app.config import settings as live_settings

    monkeypatch.setattr(live_settings, "DEBUG", False)

    resp = client.post(PATH, json={"items": [_item()]})
    assert resp.status_code == 401


# ── Caps: THIS IS THE BEHAVIOR-PINNING TEST ─────────────────────────────────
#
# Guards `MAX_OUTCOME_EVENTS_PER_REQUEST = 200` (app/telemetry/router.py).
# This test fails if that cap is ever loosened, removed, or accidentally
# turned into a 422 — verified for real: temporarily raising the constant
# to 250 flips a 201-item batch from 413 back to 202, which is recorded in
# the lane report rather than only asserted here.


def test_batch_over_200_events_is_rejected_with_413(api_client) -> None:
    client, _user_id, _admin_id = api_client
    resp = client.post(PATH, json={"items": [_item(stepIndex=i) for i in range(201)]})
    assert resp.status_code == 413


def test_batch_of_exactly_200_events_is_accepted(api_client) -> None:
    client, _user_id, _admin_id = api_client
    resp = client.post(PATH, json={"items": [_item(stepIndex=i) for i in range(200)]})
    assert resp.status_code == 202
    assert resp.json() == {"accepted": 200}


def test_empty_batch_is_rejected(api_client) -> None:
    client, _user_id, _admin_id = api_client
    resp = client.post(PATH, json={"items": []})
    assert resp.status_code == 422


def test_ms_to_answer_over_ceiling_is_rejected(api_client) -> None:
    client, _user_id, _admin_id = api_client
    resp = client.post(PATH, json={"items": [_item(msToAnswer=600_001)]})
    assert resp.status_code == 422


def test_too_many_atom_ids_is_rejected(api_client) -> None:
    client, _user_id, _admin_id = api_client
    resp = client.post(PATH, json={"items": [_item(atomIds=[f"ja:vocab:{i}" for i in range(33)])]})
    assert resp.status_code == 422


# ── Logging ──────────────────────────────────────────────────────────────


def test_logs_one_info_line_per_item_on_its_own_logger(api_client, caplog: pytest.LogCaptureFixture) -> None:
    client, _user_id, _admin_id = api_client
    caplog.set_level(logging.INFO, logger="lingo.atom_outcome")
    caplog.clear()

    resp = client.post(PATH, json={"items": [_item(), _item(stepIndex=9, correct=False, atomIds=["ja:vocab:iku"])]})
    assert resp.status_code == 202

    records = [r for r in caplog.records if r.name == "lingo.atom_outcome"]
    assert len(records) == 2
    payloads = [json.loads(r.getMessage()) for r in records]
    assert payloads[0]["type"] == "atom_outcome"
    assert payloads[0]["lang"] == "ja"
    assert payloads[0]["srcSurface"] == "lesson"
    assert payloads[1]["stepIndex"] == 9
    assert payloads[1]["correct"] is False
    assert payloads[1]["atomIds"] == ["ja:vocab:iku"]
    # Never the raw sub — hashed, same construction as `lingo.access`.
    assert all("userHash" in p and len(p["userHash"]) == 8 for p in payloads)
    assert all("sub" not in json.dumps(p) for p in payloads)


def test_never_logs_on_the_client_error_or_diag_loggers(api_client, caplog: pytest.LogCaptureFixture) -> None:
    client, _user_id, _admin_id = api_client
    caplog.set_level(logging.INFO)
    caplog.clear()
    client.post(PATH, json={"items": [_item()]})
    assert not [r for r in caplog.records if r.name in ("lingo.client_error", "lingo.client_diag")]


def test_two_different_users_get_two_different_hashes(api_client, caplog: pytest.LogCaptureFixture) -> None:
    client, _user_id, _admin_id = api_client
    caplog.set_level(logging.INFO, logger="lingo.atom_outcome")
    caplog.clear()

    client.post(PATH, json={"items": [_item()]})
    client.post(PATH, json={"items": [_item()]}, headers={"X-Dev-User": "dev|admin-user"})

    records = [r for r in caplog.records if r.name == "lingo.atom_outcome"]
    assert len(records) == 2
    hashes = {json.loads(r.getMessage())["userHash"] for r in records}
    assert len(hashes) == 2
