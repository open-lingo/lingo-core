"""SocialRepository protocol — friend graph, blocks, leaderboards.

Identity is the internal user UUID throughout. Usernames are mutable and are
only used as a lookup vector (via the existing user-by-username path) before
being resolved to a UUID.

Two backing data structures:

  ``social`` table — kind ∈ {FRIEND, REQUEST_IN, REQUEST_OUT, BLOCK}
    A row per (owner_id, kind, other_id). Mirrored writes ensure both sides
    of a relationship can be queried cheaply by owner.

  ``social_leaderboard`` table — ranked XP buckets per language/period.
    bucket format: "<langId>#<periodKey>" e.g. "ja#2026-W21" or "ja#2026-05".

See the design doc in the implementation prompt for the full motivation.
"""

from typing import Any, Protocol


class SocialRepository(Protocol):
    """Friend graph, blocks, and leaderboard backend."""

    # ── Friend graph ────────────────────────────────────────────────────────

    async def list_friends(self, user_id: str) -> list[dict[str, Any]]:
        """Return all FRIEND rows owned by ``user_id``.

        Each row: ``{other_id, created_at, metadata}``.
        Caller resolves user records for display.
        """
        ...

    async def list_friend_requests(
        self, user_id: str
    ) -> dict[str, list[dict[str, Any]]]:
        """Return ``{incoming: [...], outgoing: [...]}`` for the user.

        Each item: ``{other_id, created_at, metadata}``.
        """
        ...

    async def get_relationship(
        self, owner_id: str, other_id: str
    ) -> dict[str, Any] | None:
        """Return any relationship row from owner→other, or None.

        Used to detect existing FRIEND / REQUEST / BLOCK before inserting new
        ones. Returns the row with ``kind`` populated when found.
        """
        ...

    async def send_friend_request(
        self, from_user_id: str, to_user_id: str
    ) -> None:
        """Atomically insert REQUEST_OUT (from→to) and REQUEST_IN (to→from).

        Caller is responsible for pre-flight checks (self, block, dup).
        """
        ...

    async def accept_friend_request(
        self, accepter_id: str, requester_id: str
    ) -> bool:
        """Promote pending REQUEST rows into mutual FRIEND rows.

        Returns True on success, False if no pending request found.
        """
        ...

    async def delete_friend_request(
        self, owner_id: str, other_id: str
    ) -> bool:
        """Decline an incoming request OR cancel an outgoing one.

        Deletes both mirrored REQUEST rows if either exists.
        Returns True if any row was deleted.
        """
        ...

    async def unfriend(self, user_id: str, friend_id: str) -> bool:
        """Delete both mirrored FRIEND rows. Returns True if any row deleted."""
        ...

    # ── Blocks ──────────────────────────────────────────────────────────────

    async def block_user(self, owner_id: str, other_id: str) -> None:
        """Insert a BLOCK row (one-directional) and cascade-delete any
        FRIEND / REQUEST rows between the two users.
        """
        ...

    async def unblock_user(self, owner_id: str, other_id: str) -> bool:
        """Delete the BLOCK row. Returns True if a row was deleted."""
        ...

    async def list_blocks(self, owner_id: str) -> list[dict[str, Any]]:
        """Return all BLOCK rows owned by ``owner_id``."""
        ...

    async def is_blocked(self, owner_id: str, other_id: str) -> bool:
        """Return True if ``owner_id`` has blocked ``other_id``."""
        ...

    # ── Leaderboards ────────────────────────────────────────────────────────

    async def add_xp_to_leaderboard(
        self, user_id: str, lang: str, xp_delta: int
    ) -> None:
        """Increment the user's XP in the current week and month buckets for
        ``lang``. Caller is responsible for opt-in checks; this method just
        does the writes (UPSERT + INCREMENT).
        """
        ...

    async def get_leaderboard(
        self,
        bucket: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return the ranked entries for ``bucket`` ordered by XP desc.

        Each row: ``{user_id, xp, lessons, last_updated}``.
        """
        ...

    async def get_user_leaderboard_entry(
        self, bucket: str, user_id: str
    ) -> dict[str, Any] | None:
        """Return ``{user_id, xp, lessons, last_updated, rank, total}`` for a
        single user in a bucket, or None if the user has no entry.
        """
        ...

    async def get_friends_leaderboard(
        self, user_id: str, bucket: str
    ) -> list[dict[str, Any]]:
        """Return the user + their friends, ranked by XP desc in ``bucket``."""
        ...
