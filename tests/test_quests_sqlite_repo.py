"""SqliteQuestRepository claim-contract tests.

Locks the SQLite backend to the same transition-only ``claim`` contract the
Dynamo backend enforces (see tests/test_quests_dynamo.py). A drift between the
two backends is exactly the double-award bug class this guards against.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio

from app.db.sqlite.quests import SqliteQuestRepository


@pytest_asyncio.fixture()
async def repo() -> AsyncIterator[SqliteQuestRepository]:
    path = os.path.join(tempfile.mkdtemp(prefix="lingo-quests-repo-"), "q.db")
    r = SqliteQuestRepository(path)
    await r.connect()
    try:
        yield r
    finally:
        await r.close()


def _quest(quest_id: str, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": quest_id,
        "user_id": "u1",
        "type": "daily",
        "title_key": "t",
        "description_key": "d",
        "emoji": "⚡",
        "progress_current": 0,
        "progress_target": 50,
        "progress_unit": "XP",
        "reward_lingots": 5,
        "reward_xp": 10,
        "reward_ad_free_minutes": 0,
        "reward_streak_shield": False,
        "status": "active",
        "friend_id": None,
        "friend_display_name": None,
        "expires_at": "2099-12-31T00:00:00+00:00",
        "reward_granted": False,
        "created_at": "2026-06-01T00:00:00+00:00",
    }
    base.update(overrides)
    return base


async def test_claim_when_claimable_transitions(repo: SqliteQuestRepository) -> None:
    await repo.put_quest(_quest("q", progress_current=50, status="claimable"))
    claimed = await repo.claim("u1", "q")
    assert claimed is not None
    assert claimed["status"] == "completed"
    assert claimed["reward_granted"] is True


async def test_claim_when_already_completed_returns_none(repo: SqliteQuestRepository) -> None:
    await repo.put_quest(
        _quest("q", progress_current=50, status="completed", reward_granted=True)
    )
    assert await repo.claim("u1", "q") is None
    got = await repo.get_quest("u1", "q")
    assert got is not None and got["status"] == "completed"


async def test_claim_when_not_claimable_returns_none(repo: SqliteQuestRepository) -> None:
    await repo.put_quest(_quest("q", progress_current=10, status="active"))
    assert await repo.claim("u1", "q") is None


async def test_claim_missing_returns_none(repo: SqliteQuestRepository) -> None:
    assert await repo.claim("u1", "ghost") is None


async def test_double_claim_transitions_exactly_once(repo: SqliteQuestRepository) -> None:
    """Second claim performs no flip -> None (the exactly-once guarantee)."""
    await repo.put_quest(_quest("q", progress_current=50, status="claimable"))
    first = await repo.claim("u1", "q")
    second = await repo.claim("u1", "q")
    assert first is not None
    assert second is None
