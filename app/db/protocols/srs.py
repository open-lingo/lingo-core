from typing import Any, Protocol


class SRSRepository(Protocol):
    """Per-card SRS state. One row/item per (user, card). Supports efficient due-date queries."""

    async def get_all(self, user_id: str) -> dict[str, dict[str, Any]]:
        """Return the full SRS map: {cardId: SRSCardState}."""
        ...

    async def get_due_cards(
        self, user_id: str, on_or_before: str
    ) -> dict[str, dict[str, Any]]:
        """Return cards with dueDate <= on_or_before (YYYY-MM-DD). Efficient index-backed query."""
        ...

    async def get_card(self, user_id: str, card_id: str) -> dict[str, Any] | None:
        """Return SRS state for a single card, or None."""
        ...

    async def upsert_cards(
        self, user_id: str, cards: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """Upsert multiple card states. Last-write-wins by lastReviewDate.
        Returns the merged state for all affected cards."""
        ...

    async def delete_cards(self, user_id: str, card_ids: list[str]) -> int:
        """Remove SRS state for specific cards. Returns count deleted."""
        ...

    async def clear_all(self, user_id: str) -> int:
        """Remove all SRS state for a user. Returns count deleted."""
        ...
