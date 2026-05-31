"""Service-to-service quest routes for lingo-async callbacks.

Uses the same module-scoped client + user pattern as test_quests.py.
Auth0 is bypassed via DEBUG=true + X-Dev-User. Internal-service auth is
gated by require_internal_service which checks the INTERNAL_SERVICE_TOKEN
setting — we monkeypatch that to ``dev-secret`` for every test.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any

import aiosqlite
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

# Mutate env BEFORE importing app.config so Settings picks up our temp DB.
TMP_DB = os.path.join(tempfile.mkdtemp(prefix="lingo-quests-internal-"), "quests_internal.db")
os.environ["DB_BACKEND"] = "sqlite"
os.environ["SQLITE_PATH"] = TMP_DB
os.environ["DEBUG"] = "true"
os.environ["DEV_USER"] = "auth0|trevor-internal"

_INTERNAL_TOKEN = "dev-secret"
_INTERNAL_HEADER = {"Authorization": f"Bearer {_INTERNAL_TOKEN}"}
_DEV_SUB = "auth0|trevor-internal"


def _as(sub: str) -> dict[str, str]:
    return {"X-Dev-User": sub}


@pytest.fixture(scope="module")
def client() -> Any:
    from app.config import settings

    settings.DB_BACKEND = "sqlite"
    settings.SQLITE_PATH = TMP_DB
    settings.DEBUG = True
    settings.DEV_USER = _DEV_SUB
    settings.INTERNAL_SERVICE_TOKEN = _INTERNAL_TOKEN

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def trevor(client: TestClient) -> dict[str, Any]:
    resp = client.post(
        "/api/core/v1/users/me",
        json={"username": "trevor_internal", "display_name": "Trevor Internal"},
        headers=_as(_DEV_SUB),
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


@pytest_asyncio.fixture()
async def seeded_quest(trevor: dict[str, Any]) -> dict[str, Any]:
    """Seed one active quest for the test user directly into the DB."""
    from datetime import UTC, datetime, timedelta

    expires_at = (datetime.now(UTC) + timedelta(hours=12)).isoformat()
    created_at = datetime.now(UTC).isoformat()
    user_id = trevor["id"]
    quest_id = f"{user_id}:internal-test-quest"

    db = await aiosqlite.connect(TMP_DB)
    try:
        await db.execute(
            """INSERT INTO quests (
                id, user_id, type, title_key, description_key, emoji,
                progress_current, progress_target, progress_unit,
                reward_lingots, reward_xp, reward_ad_free_minutes,
                reward_streak_shield, status, friend_id, friend_display_name,
                expires_at, reward_granted, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                progress_current = excluded.progress_current,
                status = excluded.status,
                reward_granted = excluded.reward_granted""",
            (
                quest_id,
                user_id,
                "daily",
                "quests.daily.fiftyXp.title",
                "quests.daily.fiftyXp.desc",
                "⚡",
                0,
                50,
                "XP",
                5,
                10,
                0,
                0,
                "active",
                None,
                None,
                expires_at,
                0,
                created_at,
            ),
        )
        await db.commit()
    finally:
        await db.close()
    return {"id": quest_id, "user_id": user_id, "target": 50}


# ─── Auth gating tests ────────────────────────────────────────────────────────


def test_internal_list_requires_system_token(client: TestClient) -> None:
    """Missing Authorization header → 401."""
    resp = client.get(
        "/api/core/v1/quests/_internal/list?user_id=u-x",
        # No Authorization header
    )
    assert resp.status_code == 401, resp.text


def test_internal_list_wrong_token(client: TestClient) -> None:
    """Wrong token → 401."""
    resp = client.get(
        "/api/core/v1/quests/_internal/list?user_id=u-x",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401, resp.text


def test_internal_progress_requires_system_token(
    client: TestClient,
    seeded_quest: dict[str, Any],
) -> None:
    """Missing Authorization header on progress route → 401."""
    resp = client.post(
        f"/api/core/v1/quests/_internal/{seeded_quest['id']}/progress",
        json={"user_id": seeded_quest["user_id"], "delta": 5},
        # No Authorization header
    )
    assert resp.status_code == 401, resp.text


# ─── Functional tests ─────────────────────────────────────────────────────────


def test_internal_list_returns_quests(
    client: TestClient,
    trevor: dict[str, Any],
    seeded_quest: dict[str, Any],
) -> None:
    """/_internal/list?user_id=<id> returns the user's quests."""
    user_id = seeded_quest["user_id"]

    resp = client.get(
        f"/api/core/v1/quests/_internal/list?user_id={user_id}",
        headers=_INTERNAL_HEADER,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "items" in body
    ids = [q["id"] for q in body["items"]]
    assert seeded_quest["id"] in ids


def test_internal_progress_advances_quest(
    client: TestClient,
    seeded_quest: dict[str, Any],
) -> None:
    """/_internal/{id}/progress bumps progress and returns updated quest."""
    quest_id = seeded_quest["id"]
    user_id = seeded_quest["user_id"]

    resp = client.post(
        f"/api/core/v1/quests/_internal/{quest_id}/progress",
        headers=_INTERNAL_HEADER,
        json={"user_id": user_id, "delta": 10},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["progress"]["current"] >= 10


def test_internal_progress_missing_quest_returns_404(
    client: TestClient,
    trevor: dict[str, Any],
) -> None:
    """/_internal/{id}/progress with unknown quest_id → 404."""
    user_id = trevor["id"]
    resp = client.post(
        "/api/core/v1/quests/_internal/nonexistent-quest-id/progress",
        headers=_INTERNAL_HEADER,
        json={"user_id": user_id, "delta": 5},
    )
    assert resp.status_code == 404, resp.text


def test_existing_user_facing_routes_unaffected(
    client: TestClient,
    seeded_quest: dict[str, Any],
) -> None:
    """Verify the standard user-facing list + progress routes still work."""
    # List via user-facing route (DEBUG bypass, no X-Dev-User needed — uses DEV_USER).
    list_resp = client.get(
        "/api/core/v1/quests",
        headers=_as(_DEV_SUB),
    )
    assert list_resp.status_code == 200, list_resp.text
    assert "items" in list_resp.json()

    # Progress via user-facing route.
    quest_id = seeded_quest["id"]
    progress_resp = client.post(
        f"/api/core/v1/quests/{quest_id}/progress",
        json={"delta": 1},
        headers=_as(_DEV_SUB),
    )
    assert progress_resp.status_code == 200, progress_resp.text
