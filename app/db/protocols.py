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
    """Per-card SRS state. One row/item per (user, card). Supports efficient due-date queries."""

    async def get_all(self, auth0_id: str) -> dict[str, dict[str, Any]]:
        """Return the full SRS map: {cardId: SRSCardState}."""
        ...

    async def get_due_cards(
        self, auth0_id: str, on_or_before: str
    ) -> dict[str, dict[str, Any]]:
        """Return cards with dueDate <= on_or_before (YYYY-MM-DD). Efficient index-backed query."""
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


class DeckRepository(Protocol):
    """Deck manifest + content. Manifest = metadata; content = cards. Both keyed by deck id."""

    async def list_manifests(
        self,
        language_id: str | None = None,
        author_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return deck manifests, optionally filtered by language_id, author_id, status."""
        ...

    async def get_manifest(self, deck_id: str) -> dict[str, Any] | None:
        """Return manifest for a deck, or None."""
        ...

    async def get_deck(self, deck_id: str) -> dict[str, Any] | None:
        """Return full deck (manifest + cards). None if not found."""
        ...

    async def get_versions(self, deck_ids: list[str]) -> dict[str, str]:
        """Return {deck_id: version} for the given deck ids."""
        ...

    async def upsert_deck(
        self, deck_id: str, manifest: dict[str, Any], cards: list[dict[str, Any]]
    ) -> None:
        """Insert or update a deck (manifest + content)."""
        ...


class SubscriptionRepository(Protocol):
    """User subscriptions to content (decks, addons, stories). Separate table from settings.
    Query by auth0_id; sort/filter by content_type. Implementation can be SQLite or DynamoDB."""

    async def add(
        self, auth0_id: str, content_type: str, content_id: str
    ) -> None:
        """Add a subscription. Idempotent if already exists."""
        ...

    async def remove(
        self, auth0_id: str, content_type: str, content_id: str
    ) -> None:
        """Remove a subscription. No-op if not subscribed."""
        ...

    async def list(
        self, auth0_id: str, content_type: str | None = None
    ) -> list[dict[str, Any]]:
        """List subscriptions, optionally filtered by content_type.
        Returns list of {contentType, contentId, createdAt, enabled, newCardsPerDay, newCardOrder}."""
        ...

    async def update_settings(
        self,
        auth0_id: str,
        content_type: str,
        content_id: str,
        patch: dict[str, Any],
    ) -> bool:
        """Update subscription settings. Returns True if updated, False if not found."""
        ...

    async def has(
        self, auth0_id: str, content_type: str, content_id: str
    ) -> bool:
        """Check if user has this subscription."""
        ...
