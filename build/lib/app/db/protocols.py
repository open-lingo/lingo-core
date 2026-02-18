"""Repository protocol contracts.

Each protocol defines the interface that both the SQLite (local dev) and
DynamoDB (prod) implementations must satisfy.  FastAPI's DI system injects
whichever concrete class is active based on config.
"""

from typing import Any, Protocol


class UserRepository(Protocol):
    async def get_settings(self, user_id: str) -> dict[str, Any] | None:
        """Return the user's settings dict, or None if no record exists."""
        ...

    async def update_settings(self, user_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """Merge *patch* into the user's settings and return the full result."""
        ...

    async def get_profile(self, user_id: str) -> dict[str, Any] | None:
        """Return basic profile metadata (display name, avatar URL, etc.)."""
        ...

    async def upsert_profile(self, user_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Create or fully replace the profile record."""
        ...
