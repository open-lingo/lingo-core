"""Quest expiry + lazy reset behaviour (task: daily/weekly rollover).

Verifies the lazy re-seed path in ``_ensure_active_catalog``:
  - an expired quest is dropped and its type bucket re-seeded fresh
  - a claimed-then-expired daily comes back with zero progress + active status
  - a non-expired completed daily is NOT prematurely re-seeded (same-day)

Isolated temp DB so the module-scoped catalogue state from other quest tests
doesn't bleed in.
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

TMP_DB = os.path.join(tempfile.mkdtemp(prefix="lingo-quests-reset-"), "quests.db")
os.environ["DB_BACKEND"] = "sqlite"
os.environ["SQLITE_PATH"] = TMP_DB
os.environ["DEBUG"] = "true"
os.environ["DEV_USER"] = "auth0|trevor-reset"

_DEV_SUB = "auth0|trevor-reset"


def _as(sub: str) -> dict[str, str]:
    return {"X-Dev-User": sub}


@pytest.fixture(scope="module")
def client() -> Any:
    from app.config import settings

    settings.DB_BACKEND = "sqlite"
    settings.SQLITE_PATH = TMP_DB
    settings.DEBUG = True
    settings.DEV_USER = _DEV_SUB

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def trevor(client: TestClient) -> dict[str, Any]:
    resp = client.post(
        "/api/core/v1/users/me",
        json={"username": "trevor_reset", "display_name": "Trevor Reset"},
        headers=_as(_DEV_SUB),
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def _insert_quest(row: dict[str, Any]) -> None:
    db = await aiosqlite.connect(TMP_DB)
    try:
        cols = (
            "id, user_id, type, title_key, description_key, emoji, "
            "progress_current, progress_target, progress_unit, reward_lingots, "
            "reward_xp, reward_ad_free_minutes, reward_streak_shield, status, "
            "friend_id, friend_display_name, expires_at, reward_granted, created_at"
        )
        placeholders = ", ".join("?" * 19)
        await db.execute(
            f"INSERT OR REPLACE INTO quests ({cols}) VALUES ({placeholders})",
            (
                row["id"], row["user_id"], row["type"], row["title_key"],
                row["description_key"], row["emoji"], row["progress_current"],
                row["progress_target"], row["progress_unit"], row["reward_lingots"],
                row["reward_xp"], row["reward_ad_free_minutes"],
                row["reward_streak_shield"], row["status"], row["friend_id"],
                row["friend_display_name"], row["expires_at"], row["reward_granted"],
                row["created_at"],
            ),
        )
        await db.commit()
    finally:
        await db.close()


def _row(user_id: str, quest_id: str, qtype: str, status: str, expires_at: str, **kw: Any) -> dict[str, Any]:
    base = {
        "id": quest_id, "user_id": user_id, "type": qtype,
        "title_key": "t", "description_key": "d", "emoji": "⚡",
        "progress_current": 0, "progress_target": 50, "progress_unit": "XP",
        "reward_lingots": 5, "reward_xp": 10, "reward_ad_free_minutes": 0,
        "reward_streak_shield": 0, "status": status, "friend_id": None,
        "friend_display_name": None, "expires_at": expires_at, "reward_granted": 0,
        "created_at": datetime.now(UTC).isoformat(),
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_claimed_then_expired_daily_comes_back_fresh(
    client: TestClient, trevor: dict[str, Any]
) -> None:
    uid = trevor["id"]
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()

    # A completed+claimed daily that has now expired.
    await _insert_quest(
        _row(uid, f"{uid}:daily-fifty-xp", "daily", "completed", past,
             progress_current=50, reward_granted=1)
    )

    resp = client.get("/api/core/v1/quests", headers=_as(_DEV_SUB))
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]

    dailies = [q for q in items if q["type"] == "daily"]
    assert dailies, "daily bucket must be re-seeded after expiry"
    # The re-seeded dailies are fresh: zero progress, active.
    for q in dailies:
        assert q["status"] == "active"
        assert q["progress"]["current"] == 0
    # A weekly bucket is seeded too (never had one before).
    assert any(q["type"] == "weekly" for q in items)


@pytest.mark.asyncio
async def test_non_expired_completed_daily_not_reseeded(
    client: TestClient, trevor: dict[str, Any]
) -> None:
    uid = trevor["id"]
    # Wipe to a known state, then plant a completed-but-still-valid daily.
    client.post("/api/core/v1/quests/refresh", headers=_as(_DEV_SUB))
    db = await aiosqlite.connect(TMP_DB)
    try:
        await db.execute("DELETE FROM quests WHERE user_id = ?", (uid,))
        await db.commit()
    finally:
        await db.close()

    future = (datetime.now(UTC) + timedelta(hours=6)).isoformat()
    await _insert_quest(
        _row(uid, f"{uid}:daily-fifty-xp", "daily", "completed", future,
             progress_current=50, reward_granted=1)
    )

    items = client.get("/api/core/v1/quests", headers=_as(_DEV_SUB)).json()["items"]
    dailies = [q for q in items if q["type"] == "daily"]
    # The completed daily is still present and NOT replaced with a fresh active one.
    assert len(dailies) == 1
    assert dailies[0]["status"] == "completed"
