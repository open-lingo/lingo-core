from typing import Any, Protocol


class DeckRepository(Protocol):
    """Deck manifest + content. Manifest = metadata; content = cards. Both keyed by deck id."""

    async def list_owned_manifests(
        self,
        author_id: str,
        *,
        language_id: str | None = None,
        status: str | None = None,
        exclude_companion: bool = False,
    ) -> list[dict[str, Any]]:
        """Manifests for decks authored by ``author_id`` (editor / My Content).

        Prefer over generic ``list_manifests`` when the caller means “my decks” —
        implementations may use an Author-indexed access pattern on DynamoDB.
        """

        ...

    async def list_manifests(
        self,
        language_id: str | None = None,
        author_id: str | None = None,
        status: str | None = None,
        exclude_companion: bool = False,
    ) -> list[dict[str, Any]]:
        """Return deck manifests. If exclude_companion=True, exclude decks with companionToStoryId."""
        ...

    async def get_manifest(self, deck_id: str) -> dict[str, Any] | None:
        """Return manifest for a deck, or None."""
        ...

    async def get_deck(self, deck_id: str) -> dict[str, Any] | None:
        """Return full deck (manifest + cards). None if not found."""
        ...

    async def get_decks_batch(self, deck_ids: list[str]) -> list[dict[str, Any]]:
        """Fetch multiple decks by ID. Returns only decks that exist (no access filtering)."""
        ...

    async def get_versions(self, deck_ids: list[str]) -> dict[str, str]:
        """Return {deck_id: version} for the given deck ids."""
        ...

    async def upsert_deck(
        self, deck_id: str, manifest: dict[str, Any], cards: list[dict[str, Any]]
    ) -> None:
        """Insert or update a deck (manifest + content)."""
        ...

    async def delete_deck(self, deck_id: str) -> None:
        """Delete a deck and its content. No-op if not found."""
        ...
