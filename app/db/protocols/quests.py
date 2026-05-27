"""QuestRepository protocol.

Backs the player-facing quest system: daily/weekly/random/friend goals with
progress, expiry, and rewards. Rows are owned by a single user — fan-out to
"friend quests" is modelled by storing the friend id on the quest, not by
mirroring rows.

State machine: ``active`` -> ``claimable`` (progress.current >= target) ->
``completed`` (after explicit claim). ``expired`` is set lazily on read when
``expires_at < now`` and the quest isn't yet claimed.
"""

from typing import Any, Protocol


class QuestRepository(Protocol):
    """Per-user quest state. SQLite for dev, Dynamo stub for prod."""

    async def list_quests(self, user_id: str) -> list[dict[str, Any]]:
        """Return all non-expired quests belonging to the user, newest first."""
        ...

    async def get_quest(self, user_id: str, quest_id: str) -> dict[str, Any] | None:
        """Fetch a single quest by id, scoped to the owner."""
        ...

    async def put_quest(self, quest: dict[str, Any]) -> dict[str, Any]:
        """Upsert a quest row. Returns the persisted row."""
        ...

    async def update_progress(self, user_id: str, quest_id: str, delta: int) -> dict[str, Any] | None:
        """Bump ``progress_current`` by ``delta`` (capped at target). Flips
        status to ``claimable`` if the new value crosses the target. Returns
        the updated row, or None if the quest doesn't exist."""
        ...

    async def claim(self, user_id: str, quest_id: str) -> dict[str, Any] | None:
        """Mark the quest ``completed`` and stamp ``reward_granted`` true.
        No-op + return current row if already completed. Returns None if the
        quest doesn't exist or isn't yet claimable."""
        ...

    async def delete_user_quests(self, user_id: str, types: list[str] | None = None) -> int:
        """Delete all quests for a user (optionally filtered by type). Used
        by /quests/refresh to wipe stale dailies. Returns row count."""
        ...
