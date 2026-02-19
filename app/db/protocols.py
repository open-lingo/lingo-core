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


class SRSRepository(Protocol):
    """Per-card SRS state. One global map per user, keyed by card ID."""

    async def get_all(self, auth0_id: str) -> dict[str, dict[str, Any]]:
        """Return the full SRS map: {cardId: SRSCardState}."""
        ...

    async def get_card(self, auth0_id: str, card_id: str) -> dict[str, Any] | None:
        """Return SRS state for a single card, or None."""
        ...

    async def upsert_cards(
        self, auth0_id: str, cards: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """Upsert multiple card states. Last-write-wins by lastReviewDate.
        Returns the merged state for all affected cards."""
        ...

    async def delete_cards(self, auth0_id: str, card_ids: list[str]) -> int:
        """Remove SRS state for specific cards. Returns count deleted."""
        ...

    async def clear_all(self, auth0_id: str) -> int:
        """Remove all SRS state for a user. Returns count deleted."""
        ...
