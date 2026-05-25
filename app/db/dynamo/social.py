"""DynamoDB-backed social repository — STUB.

SQLite-first per maintainer instruction 2026-05-25; implement when SQLite path
validates. Every method raises ``NotImplementedError``.

When the SQLite path is shipped, this module will hold:
  - ``lingo_social`` single-table: PK = USER#<id>, SK = <KIND>#<other_id>
    GSI ``Other-Index`` (hash other_id) for reverse lookups.
  - ``lingo_leaderboard`` table: PK = bucket, SK = user_id
    GSI ``Bucket-XP-Index`` (hash bucket, range xp) for ranked queries.
"""

from typing import Any

_UNIMPLEMENTED = "Dynamo social repo not yet implemented"


class DynamoSocialRepository:
    def __init__(self, table_name: str, region: str) -> None:
        self._table_name = table_name
        self._region = region

    async def connect(self) -> None:
        # No-op; the stub does no I/O. Connect path will land with the real impl.
        return None

    async def close(self) -> None:
        return None

    # ── Friend graph ────────────────────────────────────────────────────────

    async def list_friends(self, user_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError(_UNIMPLEMENTED)

    async def list_friend_requests(
        self, user_id: str
    ) -> dict[str, list[dict[str, Any]]]:
        raise NotImplementedError(_UNIMPLEMENTED)

    async def get_relationship(
        self, owner_id: str, other_id: str
    ) -> dict[str, Any] | None:
        raise NotImplementedError(_UNIMPLEMENTED)

    async def send_friend_request(
        self, from_user_id: str, to_user_id: str
    ) -> None:
        raise NotImplementedError(_UNIMPLEMENTED)

    async def accept_friend_request(
        self, accepter_id: str, requester_id: str
    ) -> bool:
        raise NotImplementedError(_UNIMPLEMENTED)

    async def delete_friend_request(
        self, owner_id: str, other_id: str
    ) -> bool:
        raise NotImplementedError(_UNIMPLEMENTED)

    async def unfriend(self, user_id: str, friend_id: str) -> bool:
        raise NotImplementedError(_UNIMPLEMENTED)

    # ── Blocks ──────────────────────────────────────────────────────────────

    async def block_user(self, owner_id: str, other_id: str) -> None:
        raise NotImplementedError(_UNIMPLEMENTED)

    async def unblock_user(self, owner_id: str, other_id: str) -> bool:
        raise NotImplementedError(_UNIMPLEMENTED)

    async def list_blocks(self, owner_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError(_UNIMPLEMENTED)

    async def is_blocked(self, owner_id: str, other_id: str) -> bool:
        raise NotImplementedError(_UNIMPLEMENTED)

    # ── Leaderboards ────────────────────────────────────────────────────────

    async def add_xp_to_leaderboard(
        self, user_id: str, lang: str, xp_delta: int
    ) -> None:
        raise NotImplementedError(_UNIMPLEMENTED)

    async def get_leaderboard(
        self,
        bucket: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError(_UNIMPLEMENTED)

    async def get_user_leaderboard_entry(
        self, bucket: str, user_id: str
    ) -> dict[str, Any] | None:
        raise NotImplementedError(_UNIMPLEMENTED)

    async def get_friends_leaderboard(
        self, user_id: str, bucket: str
    ) -> list[dict[str, Any]]:
        raise NotImplementedError(_UNIMPLEMENTED)
