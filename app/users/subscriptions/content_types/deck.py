"""Deck subscription handler."""

from typing import TYPE_CHECKING, Any

from app.users.subscriptions.content_types.base import BaseContentType

if TYPE_CHECKING:
    from app.db.protocols import DeckRepository


class DeckContentType(BaseContentType):
    """Handler for deck subscriptions. Validates deck exists."""

    def __init__(self, deck_repo: "DeckRepository | None" = None):
        self._deck_repo = deck_repo

    @property
    def type_name(self) -> str:
        return "deck"

    async def validate_subscription(
        self, content_id: str, context: dict[str, Any] | None = None
    ) -> bool:
        if self._deck_repo is None:
            return True
        manifest = await self._deck_repo.get_manifest(content_id)
        return manifest is not None
