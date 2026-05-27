"""Story repository protocol."""

from typing import Any, Protocol


class StoryRepository(Protocol):
    """Story CRUD operations."""

    async def list_stories(
        self,
        author_id: str | None = None,
        language_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """List stories, optionally filtered."""
        ...

    async def get_story(self, story_id: str) -> dict[str, Any] | None:
        """Get a story by ID."""
        ...

    async def create_story(self, story_id: str, data: dict[str, Any]) -> None:
        """Create a story."""
        ...

    async def update_story(self, story_id: str, data: dict[str, Any]) -> None:
        """Update a story."""
        ...

    async def delete_story(self, story_id: str) -> None:
        """Delete a story. No-op if not found."""
        ...
