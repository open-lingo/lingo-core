"""Concurrent-claim idempotency for the quest claim endpoint.

Reproduces the double-award race: two concurrent claim requests both read the
quest as ``claimable`` before either writes ``completed``. The reward credit
must fire exactly once. Exercises the router coroutine directly with fake
repos so we can deterministically stage the race window (get_quest sees
claimable, but the atomic claim finds it already completed by the other
request).
"""

from __future__ import annotations

from typing import Any

from app.auth.schemas import TokenPayload
from app.quests.router import claim_quest


class _RaceQuestRepo:
    """Fake repo staging the concurrent-claim window.

    ``get_quest`` returns a claimable snapshot (what both racers read).
    ``claim`` behaves like the atomic backends AFTER a competing request has
    already completed the quest: the transition does not happen, so it returns
    None. The first (winning) request is represented separately below.
    """

    def __init__(self, row: dict[str, Any], *, transition_succeeds: bool) -> None:
        self._row = row
        self._transition_succeeds = transition_succeeds
        self.claim_calls = 0

    async def get_quest(self, user_id: str, quest_id: str) -> dict[str, Any] | None:
        return dict(self._row)

    async def claim(self, user_id: str, quest_id: str) -> dict[str, Any] | None:
        self.claim_calls += 1
        if self._transition_succeeds:
            completed = dict(self._row, status="completed", reward_granted=True)
            self._row = completed
            return completed
        # Lost the race: the other request already flipped it to completed.
        self._row = dict(self._row, status="completed", reward_granted=True)
        return None


class _FakeUserRepo:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row
        self.updates: list[dict[str, Any]] = []

    async def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        return dict(self.row)

    async def update_user(self, user_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        self.updates.append(patch)
        self.row.update(patch)
        return dict(self.row)


def _claimable_row() -> dict[str, Any]:
    return {
        "id": "q1",
        "user_id": "u1",
        "type": "daily",
        "title_key": "t",
        "description_key": "d",
        "emoji": "⚡",
        "progress_current": 50,
        "progress_target": 50,
        "progress_unit": "XP",
        "reward_lingots": 5,
        "reward_xp": 10,
        "reward_ad_free_minutes": 0,
        "reward_streak_shield": False,
        "status": "claimable",
        "friend_id": None,
        "friend_display_name": None,
        "expires_at": "2099-12-31T00:00:00+00:00",
        "reward_granted": False,
        "created_at": "2026-06-01T00:00:00+00:00",
    }


_USER = TokenPayload(sub="auth0|u1", id="u1")


async def test_winning_claim_grants_rewards_once() -> None:
    repo = _RaceQuestRepo(_claimable_row(), transition_succeeds=True)
    users = _FakeUserRepo({"id": "u1", "lingots": 0, "xp": 0})

    resp = await claim_quest("q1", _USER, repo, users)  # type: ignore[arg-type]

    assert resp.lingots_granted == 5
    assert resp.xp_granted == 10
    assert users.updates == [{"lingots": 5, "xp": 10}]


async def test_losing_concurrent_claim_does_not_double_award() -> None:
    """The racer that loses the atomic transition must NOT credit rewards."""
    repo = _RaceQuestRepo(_claimable_row(), transition_succeeds=False)
    users = _FakeUserRepo({"id": "u1", "lingots": 5, "xp": 10})

    resp = await claim_quest("q1", _USER, repo, users)  # type: ignore[arg-type]

    # Idempotent: the quest reads completed, but this request granted nothing.
    assert resp.quest.status == "completed"
    assert resp.lingots_granted == 0
    assert resp.xp_granted == 0
    assert users.updates == []  # no second credit to the user row
