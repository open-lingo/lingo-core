"""In-memory mock quest repository.

Used in production until ``DynamoQuestRepository`` lands. Implements
the QuestRepository protocol: list / get / put / update_progress /
claim / delete_user_quests, all backed by a process-local nested
dict keyed by (user_id, quest_id).

Data resets on Lambda cold start — same trade-off as
``MockCommunityRepository``. Quest progress accumulated mid-day
will vanish when the container recycles; replacement Dynamo impl
is the durable fix.
"""

from copy import deepcopy
from typing import Any


class MockQuestRepository:
    """In-memory implementation of QuestRepository."""

    def __init__(self) -> None:
        # quests[user_id][quest_id] = row
        self._quests: dict[str, dict[str, dict[str, Any]]] = {}

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def list_quests(self, user_id: str) -> list[dict[str, Any]]:
        user_quests = self._quests.get(user_id, {})
        rows = [deepcopy(q) for q in user_quests.values()]
        rows.sort(key=lambda q: q.get("created_at") or "", reverse=True)
        return rows

    async def get_quest(self, user_id: str, quest_id: str) -> dict[str, Any] | None:
        q = self._quests.get(user_id, {}).get(quest_id)
        return deepcopy(q) if q else None

    async def put_quest(self, quest: dict[str, Any]) -> dict[str, Any]:
        user_id = quest.get("user_id")
        quest_id = quest.get("id")
        if not user_id or not quest_id:
            return quest
        self._quests.setdefault(user_id, {})[quest_id] = dict(quest)
        return deepcopy(quest)

    async def update_progress(self, user_id: str, quest_id: str, delta: int) -> dict[str, Any] | None:
        row = self._quests.get(user_id, {}).get(quest_id)
        if not row:
            return None
        target = int(row.get("target") or row.get("progress_target") or 0)
        current = int(row.get("progress_current") or 0)
        new_current = min(target, current + int(delta)) if target > 0 else current + int(delta)
        row["progress_current"] = new_current
        if target > 0 and new_current >= target and row.get("status") == "active":
            row["status"] = "claimable"
        return deepcopy(row)

    async def claim(self, user_id: str, quest_id: str) -> dict[str, Any] | None:
        # Transition-only: return the freshly-completed row ONLY when this call
        # flips claimable -> completed; None otherwise (missing, not-claimable,
        # already-completed). Matches the SQLite/Dynamo contract so the router
        # credits rewards exactly once.
        row = self._quests.get(user_id, {}).get(quest_id)
        if not row or row.get("status") != "claimable":
            return None
        row["status"] = "completed"
        row["reward_granted"] = True
        return deepcopy(row)

    async def delete_user_quests(self, user_id: str, types: list[str] | None = None) -> int:
        user_quests = self._quests.get(user_id)
        if not user_quests:
            return 0
        if types is None:
            count = len(user_quests)
            self._quests[user_id] = {}
            return count
        type_set = set(types)
        to_delete = [qid for qid, q in user_quests.items() if q.get("type") in type_set]
        for qid in to_delete:
            del user_quests[qid]
        return len(to_delete)
