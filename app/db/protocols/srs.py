from typing import Any, Protocol


class SRSRepository(Protocol):
    """Per-card SRS state. One row/item per (user, card). Supports efficient due-date queries."""

    async def get_all(self, user_id: str) -> dict[str, dict[str, Any]]:
        """Return the full SRS map: {cardId: SRSCardState}."""
        ...

    async def get_due_cards(self, user_id: str, on_or_before: str) -> dict[str, dict[str, Any]]:
        """Return cards with dueDate <= on_or_before (YYYY-MM-DD). Efficient index-backed query."""
        ...

    async def get_card(self, user_id: str, card_id: str) -> dict[str, Any] | None:
        """Return SRS state for a single card, or None."""
        ...

    async def upsert_cards(
        self, user_id: str, cards: dict[str, dict[str, Any]]
    ) -> tuple[dict[str, dict[str, Any]], list[str]]:
        """Upsert multiple card states. Last-write-wins by lastReviewDate.

        Returns ``(merged, failed_card_ids)``: ``merged`` holds the
        authoritative post-write state for every card that succeeded;
        ``failed_card_ids`` lists ids whose individual write raised (a repo
        error unrelated to the LWW race, which is handled internally and
        never surfaces here). A card's absence from BOTH is impossible — every
        input id lands in exactly one of the two.

        Implementations MUST NOT let one card's exception abort the others:
        the per-card writes are fanned out concurrently, and a bare
        ``asyncio.gather`` (no ``return_exceptions=True``) raises on the
        first failure while leaving sibling tasks scheduled-but-unawaited —
        on Lambda, the invocation can return (and the execution environment
        freeze) before those orphaned tasks run, silently dropping writes
        that never even got a chance to fail. See
        docs/progress-sync-contract-2026-09-17.md.
        """
        ...

    async def delete_cards(self, user_id: str, card_ids: list[str]) -> int:
        """Remove SRS state for specific cards. Returns count deleted."""
        ...

    async def clear_all(self, user_id: str) -> int:
        """Remove all SRS state for a user. Returns count deleted."""
        ...
