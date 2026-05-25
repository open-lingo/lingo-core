"""SocialRepository protocol.

Backs the /social router: friends, friend requests, blocks, leaderboards,
activity feed (with reactions), invite codes + redemptions, threads + messages,
and friend-quest target heuristics. The protocol stays storage-agnostic; SQLite
and DynamoDB impls live in app/db/sqlite/social.py and app/db/dynamo/social.py.

All ids are internal user UUIDs unless otherwise noted; ``code`` for invites is
an 8-char alphanumeric scoped to its owner.
"""

from typing import Any, Protocol


class SocialRepository(Protocol):
    # ── Friends ──────────────────────────────────────────────────────────────

    async def list_friends(self, user_id: str) -> list[dict[str, Any]]:
        """Friend edges for ``user_id``. Items contain ``friend_id`` + ``friended_at``."""
        ...

    async def is_friend(self, user_id: str, other_id: str) -> bool:
        ...

    async def add_friend_edge(self, a_id: str, b_id: str) -> None:
        """Create reciprocal friend edges. Idempotent."""
        ...

    async def remove_friend_edge(self, a_id: str, b_id: str) -> None:
        """Remove reciprocal friend edges. No-op if absent."""
        ...

    # ── Friend requests ──────────────────────────────────────────────────────

    async def list_friend_requests(
        self, user_id: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Returns (incoming, outgoing) request rows."""
        ...

    async def get_friend_request(
        self, from_id: str, to_id: str
    ) -> dict[str, Any] | None:
        ...

    async def upsert_friend_request(self, from_id: str, to_id: str) -> dict[str, Any]:
        ...

    async def delete_friend_request(self, from_id: str, to_id: str) -> None:
        ...

    # ── Blocks ───────────────────────────────────────────────────────────────

    async def list_blocks(self, user_id: str) -> list[dict[str, Any]]:
        ...

    async def is_blocked(self, blocker_id: str, blocked_id: str) -> bool:
        ...

    async def block_user(self, blocker_id: str, blocked_id: str) -> None:
        ...

    async def unblock_user(self, blocker_id: str, blocked_id: str) -> None:
        ...

    # ── Activity feed + reactions ────────────────────────────────────────────

    async def list_activity(
        self,
        user_id: str,
        friend_ids: list[str],
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Return recent activity for the user + their friends. Newest first."""
        ...

    async def get_activity(self, activity_id: str) -> dict[str, Any] | None:
        ...

    async def put_activity(self, activity: dict[str, Any]) -> dict[str, Any]:
        """Insert (or upsert) an activity item. Used by tests / seed."""
        ...

    async def list_reactions(
        self, activity_id: str
    ) -> list[dict[str, Any]]:
        """All reaction rows for one activity. Each row: kind, user_id, created_at."""
        ...

    async def list_reactions_bulk(
        self, activity_ids: list[str]
    ) -> dict[str, list[dict[str, Any]]]:
        """Bulk fetch reactions for many activities. Returns {activity_id: [rows]}."""
        ...

    async def toggle_reaction(
        self, activity_id: str, user_id: str, kind: str
    ) -> tuple[bool, int]:
        """Toggle the (activity_id, user_id, kind) reaction. Returns (mine, count_after)."""
        ...

    # ── Invites ──────────────────────────────────────────────────────────────

    async def get_invite_code_for_owner(self, owner_id: str) -> dict[str, Any] | None:
        ...

    async def create_invite_code(self, owner_id: str, code: str) -> dict[str, Any]:
        ...

    async def get_invite_code(self, code: str) -> dict[str, Any] | None:
        ...

    async def count_redemptions_for_owner_in_month(
        self, owner_id: str, year_month: str
    ) -> int:
        """``year_month`` is ``YYYY-MM``. Counts redemptions whose status != 'invalid'."""
        ...

    async def get_redemption(
        self, code: str, invitee_id: str
    ) -> dict[str, Any] | None:
        ...

    async def upsert_redemption(self, redemption: dict[str, Any]) -> dict[str, Any]:
        ...

    # ── Threads (stub messaging) ─────────────────────────────────────────────

    async def list_threads_for_user(self, user_id: str) -> list[dict[str, Any]]:
        ...

    async def get_thread(self, thread_id: str) -> dict[str, Any] | None:
        ...

    async def put_thread(self, thread: dict[str, Any]) -> dict[str, Any]:
        ...

    async def list_messages(self, thread_id: str) -> list[dict[str, Any]]:
        ...

    async def put_message(self, message: dict[str, Any]) -> dict[str, Any]:
        ...
