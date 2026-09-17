"""SRS sync integration tests — FSRS-6 modal shape through the API."""

import os
import tempfile

import pytest
from httpx import ASGITransport, AsyncClient

os.environ["DEBUG"] = "true"
os.environ["DB_BACKEND"] = "sqlite"

from app.auth.dependencies import get_registered_user
from app.auth.schemas import TokenPayload
from app.db.provider import get_srs_repo
from app.db.sqlite.srs import SqliteSRSRepository
from app.main import app

_TEST_USER = TokenPayload(sub="test|user", id="test-user-uuid")

_CARD_STATE = {
    "recognition": {
        "stability": 2.5,
        "difficulty": 4.1,
        "state": "review",
        "interval": 3,
        "dueDate": "2026-06-01",
        "lastReviewDate": "2026-05-28",
        "reps": 5,
        "lapses": 0,
    },
    "production": {
        "stability": 1.2,
        "difficulty": 5.0,
        "state": "learning",
        "interval": 1,
        "dueDate": "2026-05-30",
        "lastReviewDate": "2026-05-29",
        "reps": 2,
        "lapses": 1,
        "learningSteps": 1,
    },
    "lastSyncedAt": "2026-05-29T12:00:00Z",
}


@pytest.fixture
async def client():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.db")
        repo = SqliteSRSRepository(db_path)
        await repo.connect()

        app.dependency_overrides[get_registered_user] = lambda: _TEST_USER
        app.dependency_overrides[get_srs_repo] = lambda: repo

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

        app.dependency_overrides.clear()
        await repo.close()


async def test_sync_accepts_fsrs6_modal_state(client: AsyncClient):
    resp = await client.post(
        "/api/core/v1/srs/sync",
        json={"cards": {"card-1": _CARD_STATE}, "syncedAt": "2026-05-29T12:00:00Z"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "card-1" in body["cards"]
    card = body["cards"]["card-1"]
    assert card["recognition"]["stability"] == 2.5
    assert card["production"]["state"] == "learning"
    assert card["production"]["learningSteps"] == 1
    assert "syncedAt" in body


async def test_get_state_returns_fsrs6_shape(client: AsyncClient):
    await client.post(
        "/api/core/v1/srs/sync",
        json={"cards": {"card-1": _CARD_STATE}},
    )
    resp = await client.get("/api/core/v1/srs/state")
    assert resp.status_code == 200
    cards = resp.json()["cards"]
    assert "card-1" in cards
    assert cards["card-1"]["recognition"]["reps"] == 5
    assert cards["card-1"]["production"]["lapses"] == 1


async def test_due_cards_uses_min_modality_date(client: AsyncClient):
    await client.post(
        "/api/core/v1/srs/sync",
        json={"cards": {"card-1": _CARD_STATE}},
    )
    resp = await client.get(
        "/api/core/v1/srs/due",
        params={"on_or_before": "2026-05-30"},
    )
    assert resp.status_code == 200
    assert "card-1" in resp.json()["cards"]

    resp2 = await client.get(
        "/api/core/v1/srs/due",
        params={"on_or_before": "2026-05-29"},
    )
    assert "card-1" not in resp2.json()["cards"]


async def test_last_write_wins_by_max_modality_review(client: AsyncClient):
    await client.post(
        "/api/core/v1/srs/sync",
        json={"cards": {"card-1": _CARD_STATE}},
    )
    older = {
        **_CARD_STATE,
        "recognition": {**_CARD_STATE["recognition"], "lastReviewDate": "2026-05-27"},
        "production": {**_CARD_STATE["production"], "lastReviewDate": "2026-05-27"},
    }
    resp = await client.post(
        "/api/core/v1/srs/sync",
        json={"cards": {"card-1": older}},
    )
    card = resp.json()["cards"]["card-1"]
    assert card["recognition"]["lastReviewDate"] == "2026-05-28"

    newer = {
        **_CARD_STATE,
        "recognition": {**_CARD_STATE["recognition"], "lastReviewDate": "2026-06-01"},
        "production": {**_CARD_STATE["production"], "lastReviewDate": "2026-06-01"},
    }
    resp2 = await client.post(
        "/api/core/v1/srs/sync",
        json={"cards": {"card-1": newer}},
    )
    card2 = resp2.json()["cards"]["card-1"]
    assert card2["recognition"]["lastReviewDate"] == "2026-06-01"


async def test_bury_updates_even_when_server_newer(client: AsyncClient):
    await client.post(
        "/api/core/v1/srs/sync",
        json={"cards": {"card-1": _CARD_STATE}},
    )
    older_with_bury = {
        **_CARD_STATE,
        "recognition": {**_CARD_STATE["recognition"], "lastReviewDate": "2026-05-20"},
        "production": {**_CARD_STATE["production"], "lastReviewDate": "2026-05-20"},
        "buriedUntil": "2026-07-01",
    }
    resp = await client.post(
        "/api/core/v1/srs/sync",
        json={"cards": {"card-1": older_with_bury}},
    )
    card = resp.json()["cards"]["card-1"]
    assert card["buriedUntil"] == "2026-07-01"
    assert card["recognition"]["lastReviewDate"] == "2026-05-28"


async def test_delete_and_clear(client: AsyncClient):
    await client.post(
        "/api/core/v1/srs/sync",
        json={"cards": {"a": _CARD_STATE, "b": _CARD_STATE}},
    )
    resp = await client.request(
        "DELETE",
        "/api/core/v1/srs/cards",
        json={"cardIds": ["a"]},
    )
    assert resp.json()["deleted"] == 1

    state = (await client.get("/api/core/v1/srs/state")).json()
    assert "a" not in state["cards"]
    assert "b" in state["cards"]

    resp2 = await client.request("DELETE", "/api/core/v1/srs/all")
    assert resp2.json()["deleted"] == 1


async def test_one_card_failure_returns_partial_success_not_500(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-09-17 correctness audit. One card's repo error must not turn the
    whole /srs/sync request into a bare 500 that reports nothing for the
    OTHER cards in the same push — those cards' writes already landed.
    """
    # Deliberately NOT a fresh `from app.db.provider import get_srs_repo`
    # here: other test modules in the same session (test_progress.py's
    # `api_client` fixture) `importlib.reload(provider_mod)`, which replaces
    # `app.db.provider.get_srs_repo` with a new function object. A re-import
    # inside this test body would pick up that later object, which is not
    # `==` the one this file's `client` fixture used as the
    # `dependency_overrides` dict key (imported once at module load, above)
    # — a fresh import here 500s the lookup with a KeyError that only shows
    # up when this file runs alongside test_progress.py, not in isolation.
    # Reuse the module-level import (`get_srs_repo` at the top of this file)
    # instead, which is guaranteed to be the same object used as the key.
    repo = app.dependency_overrides[get_srs_repo]()
    real_get_card = repo.get_card

    async def flaky_get_card(user_id, card_id):
        if card_id == "card-boom":
            raise RuntimeError("simulated repo failure for this card only")
        return await real_get_card(user_id, card_id)

    monkeypatch.setattr(repo, "get_card", flaky_get_card)

    resp = await client.post(
        "/api/core/v1/srs/sync",
        json={
            "cards": {"card-ok": _CARD_STATE, "card-boom": _CARD_STATE},
            "syncedAt": "2026-05-29T12:00:00Z",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "card-ok" in body["cards"]
    assert "card-boom" not in body["cards"]
    assert body["failedCardIds"] == ["card-boom"]

    # The failed card must not have been silently written either.
    monkeypatch.setattr(repo, "get_card", real_get_card)
    state = (await client.get("/api/core/v1/srs/state")).json()
    assert "card-ok" in state["cards"]
    assert "card-boom" not in state["cards"]
