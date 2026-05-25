"""SRS API tests — verifies FSRS-6 modal blob round-trips through the
schema, router, and SQLite repo intact."""

import pytest


def _modal_state(last_review_date: str = "2026-05-25") -> dict:
    """An FSRS-6 modal payload — the shape the FE actually ships.

    The merge winner is derived from the per-modality ``lastReviewDate``
    fields (see ``_max_last_review`` in the SQLite repo), not a redundant
    top-level key.
    """
    return {
        "recognition": {
            "stability": 1.5,
            "difficulty": 5.2,
            "state": "learning",
            "interval": 1,
            "dueDate": "2026-05-26",
            "lastReviewDate": last_review_date,
            "reps": 1,
            "lapses": 0,
            "learningSteps": 1,
        },
        "production": {
            "stability": 0.5,
            "difficulty": 6.0,
            "state": "new",
            "interval": 0,
            "dueDate": "2026-05-26",
            "lastReviewDate": last_review_date,
            "reps": 0,
            "lapses": 0,
        },
    }


def test_sync_round_trip(api_client) -> None:
    """FSRS-6 modal payload posted via /srs/sync must round-trip via /srs/state."""
    client, _user_id, _admin = api_client

    payload = {"cards": {"card-1": _modal_state()}}
    resp = client.post("/api/core/v1/srs/sync", json=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "card-1" in body["cards"]
    returned = body["cards"]["card-1"]
    assert returned["recognition"]["stability"] == 1.5
    assert returned["recognition"]["lastReviewDate"] == "2026-05-25"
    assert returned["production"]["state"] == "new"

    # GET /srs/state must return the same payload.
    resp = client.get("/api/core/v1/srs/state")
    assert resp.status_code == 200, resp.text
    state = resp.json()
    assert "card-1" in state["cards"]
    got = state["cards"]["card-1"]
    assert got["recognition"]["stability"] == 1.5
    assert got["recognition"]["difficulty"] == 5.2
    assert got["recognition"]["state"] == "learning"
    assert got["production"]["state"] == "new"
    assert got["production"]["lastReviewDate"] == "2026-05-25"


def test_sync_last_write_wins(api_client) -> None:
    """When the server has a newer lastReviewDate, the server copy wins."""
    client, _, _ = api_client

    older = _modal_state("2026-05-24")
    older["recognition"]["stability"] = 99.0  # marker we should NOT see after merge

    newer = _modal_state("2026-05-25")
    newer["recognition"]["stability"] = 7.5  # marker we SHOULD see

    # Push newer first.
    resp = client.post("/api/core/v1/srs/sync", json={"cards": {"c": newer}})
    assert resp.status_code == 200, resp.text

    # Push older — server should keep its newer state.
    resp = client.post("/api/core/v1/srs/sync", json={"cards": {"c": older}})
    assert resp.status_code == 200, resp.text
    merged = resp.json()["cards"]["c"]
    assert merged["recognition"]["stability"] == 7.5
    assert merged["recognition"]["lastReviewDate"] == "2026-05-25"


def test_sync_rejects_payload_missing_modalities(api_client) -> None:
    """recognition and production are required; missing them must 422."""
    client, _, _ = api_client

    bad = {"buriedUntil": "2026-06-01"}
    resp = client.post("/api/core/v1/srs/sync", json={"cards": {"c": bad}})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_sqlite_repo_payload_storage_round_trip(sqlite_srs_repo) -> None:
    """Direct repo test — payload survives upsert + get_all."""
    state = _modal_state()
    merged = await sqlite_srs_repo.upsert_cards("user-x", {"card-1": state})
    assert merged["card-1"]["recognition"]["stability"] == 1.5

    state2 = await sqlite_srs_repo.get_all("user-x")
    assert state2["card-1"]["recognition"]["stability"] == 1.5
    assert state2["card-1"]["production"]["state"] == "new"
    assert state2["card-1"]["recognition"]["lastReviewDate"] == "2026-05-25"
