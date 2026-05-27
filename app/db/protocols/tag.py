"""TagRepository protocol.

Backs the /tags router. Tags are admin-curated, canonical slugs (e.g.
``jlpt-n5``, ``kdrama``) that decks can opt into for community browse
facets and discovery.

Storage shape (SQLite reference impl):

  ``tags``       — one row per canonical tag (slug PK)
  ``deck_tags``  — many-to-many join (deck_id, tag_slug)

The protocol stays storage-agnostic so the Dynamo cut-over can mirror it
with a single-table layout (PK=SLUG#<slug> SK=META plus DECK#<id>/TAG#<slug>
mirror rows) without changing the router.
"""

from typing import Any, Protocol


class TagRepository(Protocol):
    # ── Canonical tags ───────────────────────────────────────────────────────

    async def list_tags(self) -> list[dict[str, Any]]:
        """Return all canonical tags. Items: slug, display_name, description, color, created_at."""
        ...

    async def get_tag(self, slug: str) -> dict[str, Any] | None:
        """Single canonical tag or None."""
        ...

    async def create_tag(
        self,
        slug: str,
        display_name: str,
        description: str | None = None,
        color: str | None = None,
    ) -> dict[str, Any]:
        """Create a new canonical tag. Raises ValueError on duplicate slug."""
        ...

    async def update_tag(
        self,
        slug: str,
        *,
        display_name: str | None = None,
        description: str | None = None,
        color: str | None = None,
    ) -> dict[str, Any] | None:
        """Patch a canonical tag. Returns the updated row, or None if missing."""
        ...

    async def delete_tag(self, slug: str) -> bool:
        """Delete a canonical tag (cascades to deck_tags). Returns True if deleted."""
        ...

    # ── Deck ↔ tag join ──────────────────────────────────────────────────────

    async def list_tags_for_deck(self, deck_id: str) -> list[str]:
        """Return tag slugs attached to a deck, lexically sorted."""
        ...

    async def list_tags_for_decks(self, deck_ids: list[str]) -> dict[str, list[str]]:
        """Bulk variant — {deck_id: [slugs]}. Missing decks return []."""
        ...

    async def list_decks_for_tag(self, slug: str) -> list[str]:
        """Reverse lookup — deck_ids that carry this tag."""
        ...

    async def set_deck_tags(self, deck_id: str, tag_slugs: list[str]) -> None:
        """Replace the deck's tag set with ``tag_slugs`` (deduped). No validation —
        the caller is expected to have already verified slugs exist."""
        ...
