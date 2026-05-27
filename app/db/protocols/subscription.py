from typing import Any, Protocol


class SubscriptionRepository(Protocol):
    """User subscriptions to content (decks, addons, stories).
    Query by auth0_id; sort/filter by content_type."""

    async def add(self, user_id: str, content_type: str, content_id: str) -> None:
        """Add a subscription. Idempotent if already exists."""
        ...

    async def remove(self, user_id: str, content_type: str, content_id: str) -> None:
        """Remove a subscription. No-op if not subscribed."""
        ...

    async def list(self, user_id: str, content_type: str | None = None) -> list[dict[str, Any]]:
        """List subscriptions, optionally filtered by content_type.
        Returns list of {contentType, contentId, createdAt, enabled, newCardsPerDay, newCardOrder}."""
        ...

    async def update_settings(
        self,
        user_id: str,
        content_type: str,
        content_id: str,
        patch: dict[str, Any],
    ) -> bool:
        """Update subscription settings. Returns True if updated, False if not found."""
        ...

    async def has(self, user_id: str, content_type: str, content_id: str) -> bool:
        """Check if user has this subscription."""
        ...
