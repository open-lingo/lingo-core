"""Admin audit log — Protocol for the repo backing /admin/audit.

Append-only log of admin actions. Append from the ban/unban/award-xp/
deck-status/story-status endpoints; query from /admin/audit.
"""

from typing import Any, Protocol


class AuditRepository(Protocol):
    async def append(
        self,
        *,
        actor_id: str,
        action: str,
        target_id: str | None,
        target_kind: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append an audit entry and return the persisted row."""
        ...

    async def list(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
        actor_id: str | None = None,
        target_kind: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """List entries newest-first. Returns (rows, next_cursor)."""
        ...
