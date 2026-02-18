"""Repository protocol contracts.

Each protocol defines the interface that both the SQLite (local dev) and
DynamoDB (prod) implementations must satisfy.  FastAPI's DI system injects
whichever concrete class is active based on config.
"""

from typing import Any, Protocol


class UserRepository(Protocol):
    # -- User record (core identity) --

    async def create_user(self, user: dict[str, Any]) -> dict[str, Any]:
        """Insert a new user record. Raises if auth0_id already exists."""
        ...

    async def get_user_by_auth0_id(self, auth0_id: str) -> dict[str, Any] | None:
        """Look up a user by their Auth0 sub claim."""
        ...

    async def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        """Look up a user by internal UUID."""
        ...

    async def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        """Look up a user by unique username (for public profiles, etc.)."""
        ...

    async def update_user(self, auth0_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """Merge *patch* into the user record and return the full result."""
        ...

    # -- User settings (preferences blob) --

    async def get_settings(self, auth0_id: str) -> dict[str, Any] | None:
        """Return the user's settings dict, or None if no record exists."""
        ...

    async def update_settings(self, auth0_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """Merge *patch* into the user's settings and return the full result."""
        ...
