"""Quests API happy-path tests.

Boots the full FastAPI app with SQLite on a temp DB, registers a dev user,
inserts a hand-crafted quest row, and exercises list / progress / claim /
refresh against the live router. Auth is short-circuited via DEBUG=true +
the ``X-Dev-User`` header.
"""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime, timedelta
from typing import Any

import aiosqlite
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

# Mutate env BEFORE importing app.config so Settings picks up our temp DB.
TMP_DB = os.path.join(tempfile.mkdtemp(prefix="lingo-quests-"), "quests.db")
os.environ["DB_BACKEND"] = "sqlite"
os.environ["SQLITE_PATH"] = TMP_DB
os.environ["DEBUG"] = "true"
os.environ["DEV_USER"] = "auth0|trevor-quests"


def _as(sub: str) -> dict[str, str]:
    return {"X-Dev-User": sub}


@pytest.fixture(scope="module")
def client() -> Any:
    # Lazy-import after env mutation so settings see DEBUG=true + temp DB.
    from app.config import settings

    settings.DB_BACKEND = "sqlite"
    settings.SQLITE_PATH = TMP_DB
    settings.DEBUG = True
    settings.DEV_USER = "auth0|trevor-quests"

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def trevor(client: TestClient) -> dict[str, Any]:
    resp = client.post(
        "/api/core/v1/users/me",
        json={"username": "trevor_quests", "display_name": "Trevor"},
        headers=_as("auth0|trevor-quests"),
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


@pytest_asyncio.fixture()
async def seeded_quest(trevor: dict[str, Any]) -> dict[str, Any]:
    """Insert one claimable quest directly into the DB for the test user."""
    expires_at = (datetime.now(UTC) + timedelta(hours=12)).isoformat()
    created_at = datetime.now(UTC).isoformat()
    quest_id = "test-quest-fifty-xp"
    db = await aiosqlite.connect(TMP_DB)
    try:
        # Match the SqliteQuestRepository schema. The table exists already
        # because provider.init_repositories() ran on app startup.
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
                trevor["id"],
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
    return {"id": quest_id, "target": 50}


def test_list_quests_includes_seeded_row(client: TestClient, trevor: dict[str, Any], seeded_quest: dict[str, Any]) -> None:
    resp = client.get("/api/core/v1/quests", headers=_as("auth0|trevor-quests"))
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert "items" in payload
    ids = [q["id"] for q in payload["items"]]
    assert seeded_quest["id"] in ids
    target = next(q for q in payload["items"] if q["id"] == seeded_quest["id"])
    assert target["progress"]["target"] == seeded_quest["target"]
    assert target["status"] == "active"


def test_list_quests_auto_seeds_default_catalog_on_empty(
    client: TestClient, trevor: dict[str, Any]
) -> None:
    """Fresh user with no quests gets the default catalogue lazily.

    Avoids needing a scheduled job to mint per-user quests at midnight.
    """
    resp = client.get("/api/core/v1/quests", headers=_as("auth0|trevor-quests"))
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    types = {q["type"] for q in items}
    # The default catalogue covers both daily and weekly buckets.
    assert "daily" in types
    assert "weekly" in types
    # Second call doesn't re-seed (idempotent within the active window).
    resp2 = client.get("/api/core/v1/quests", headers=_as("auth0|trevor-quests"))
    assert resp2.status_code == 200
    assert len(resp2.json()["items"]) == len(items)


def test_progress_bumps_and_flips_to_claimable(client: TestClient, seeded_quest: dict[str, Any]) -> None:
    # Bump halfway — stays active.
    resp = client.post(
        f"/api/core/v1/quests/{seeded_quest['id']}/progress",
        json={"delta": 25},
        headers=_as("auth0|trevor-quests"),
    )
    assert resp.status_code == 200, resp.text
    quest = resp.json()
    assert quest["progress"]["current"] == 25
    assert quest["status"] == "active"

    # Bump past target — flips to claimable.
    resp = client.post(
        f"/api/core/v1/quests/{seeded_quest['id']}/progress",
        json={"delta": 30},
        headers=_as("auth0|trevor-quests"),
    )
    assert resp.status_code == 200, resp.text
    quest = resp.json()
    assert quest["progress"]["current"] == seeded_quest["target"]
    assert quest["status"] == "claimable"


def test_claim_grants_rewards_and_marks_completed(client: TestClient, seeded_quest: dict[str, Any]) -> None:
    # Bring the quest to claimable first.
    client.post(
        f"/api/core/v1/quests/{seeded_quest['id']}/progress",
        json={"delta": 999},
        headers=_as("auth0|trevor-quests"),
    )

    me_before = client.get("/api/core/v1/users/me", headers=_as("auth0|trevor-quests")).json()
    lingots_before = int(me_before.get("lingots") or 0)
    xp_before = int(me_before.get("xp") or 0)

    resp = client.post(
        f"/api/core/v1/quests/{seeded_quest['id']}/claim",
        headers=_as("auth0|trevor-quests"),
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["quest"]["status"] == "completed"
    assert payload["rewardGranted"] is True
    assert payload["lingotsGranted"] >= 0
    assert payload["xpGranted"] >= 0

    me_after = client.get("/api/core/v1/users/me", headers=_as("auth0|trevor-quests")).json()
    assert int(me_after.get("lingots") or 0) >= lingots_before
    assert int(me_after.get("xp") or 0) >= xp_before

    # Second claim is idempotent — returns 200 with the same completed shape.
    resp2 = client.post(
        f"/api/core/v1/quests/{seeded_quest['id']}/claim",
        headers=_as("auth0|trevor-quests"),
    )
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["quest"]["status"] == "completed"


def test_refresh_reseeds_default_catalog(client: TestClient, trevor: dict[str, Any]) -> None:
    resp = client.post("/api/core/v1/quests/refresh", headers=_as("auth0|trevor-quests"))
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["seeded"] >= 1

    # The list endpoint should now reflect the new catalog.
    list_resp = client.get("/api/core/v1/quests", headers=_as("auth0|trevor-quests"))
    assert list_resp.status_code == 200, list_resp.text
    items = list_resp.json()["items"]
    assert any(q["type"] == "daily" for q in items)
