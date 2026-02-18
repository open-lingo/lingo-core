"""Repository protocol for community/forum data.

Defines the interface for community tables (separate from users).
Implementations: MockCommunityRepository (in-memory), SqliteCommunityRepository,
DynamoCommunityRepository. Real DB implementations to be added later.
"""

from typing import Any, Protocol


class CommunityRepository(Protocol):
    """Interface for community forum and content storage.

    Uses a separate table/collection from users (e.g. forum_*, community_*).
    Markdown body content is stored as text; markdown files use the file storage methods.
    """

    # ── Forum categories ──

    async def list_categories(self) -> list[dict[str, Any]]:
        """List all forum categories, ordered by sort_order."""
        ...

    async def get_category_by_id(self, category_id: str) -> dict[str, Any] | None:
        """Look up a category by id."""
        ...

    async def get_category_by_slug(self, slug: str) -> dict[str, Any] | None:
        """Look up a category by slug."""
        ...

    # ── Forum tags ──

    async def list_tags(self) -> list[dict[str, Any]]:
        """List all forum tags."""
        ...

    async def get_tag_by_id(self, tag_id: str) -> dict[str, Any] | None:
        """Look up a tag by id."""
        ...

    async def create_tag(self, tag: dict[str, Any]) -> dict[str, Any]:
        """Create a new tag."""
        ...

    # ── Forum threads ──

    async def create_thread(self, thread: dict[str, Any]) -> dict[str, Any]:
        """Insert a new thread. Returns full record with id, created_at, etc."""
        ...

    async def get_thread_by_id(self, thread_id: str) -> dict[str, Any] | None:
        """Look up a thread by id, including author info if denormalized."""
        ...

    async def list_threads(
        self,
        *,
        category_id: str | None = None,
        tag_id: str | None = None,
        content_type: str | None = None,
        content_id: str | None = None,
        sort: str = "hot",
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List threads with optional filters. sort: 'hot' | 'new'."""
        ...

    async def update_thread(self, thread_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """Merge patch into thread and return full record."""
        ...

    async def increment_thread_views(self, thread_id: str) -> None:
        """Increment view_count for a thread."""
        ...

    # ── Forum posts (replies) ──

    async def create_post(self, post: dict[str, Any]) -> dict[str, Any]:
        """Insert a new post. Returns full record."""
        ...

    async def get_post_by_id(self, post_id: str) -> dict[str, Any] | None:
        """Look up a post by id."""
        ...

    async def list_posts_by_thread(
        self,
        thread_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List posts for a thread, ordered by created_at."""
        ...

    async def update_post(self, post_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """Merge patch into post and return full record."""
        ...

    # ── Thread–tag association ──

    async def set_thread_tags(self, thread_id: str, tag_ids: list[str]) -> None:
        """Replace all tags for a thread with the given list."""
        ...

    async def get_thread_tag_ids(self, thread_id: str) -> list[str]:
        """Get tag ids for a thread."""
        ...

    # ── Content links (polymorphic: thread → official/community content) ──

    async def add_content_link(
        self,
        thread_id: str,
        content_type: str,
        content_id: str,
        language_id: str | None = None,
    ) -> dict[str, Any]:
        """Link thread to content. content_type: official_course, addon, flashcard_pack, etc."""
        ...

    async def list_content_links_by_thread(self, thread_id: str) -> list[dict[str, Any]]:
        """Get all content links for a thread."""
        ...

    async def list_threads_by_content(
        self,
        content_type: str,
        content_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Get threads linked to specific content."""
        ...

    # ── Votes ──

    async def upsert_vote(
        self,
        user_id: str,
        target_type: str,
        target_id: str,
        value: int,
    ) -> None:
        """Record or update a vote. target_type: 'thread' | 'post'. value: 1 | -1."""
        ...

    async def get_user_vote(
        self,
        user_id: str,
        target_type: str,
        target_id: str,
    ) -> int | None:
        """Get user's vote for a target. Returns 1, -1, or None."""
        ...

    async def remove_vote(
        self,
        user_id: str,
        target_type: str,
        target_id: str,
    ) -> None:
        """Remove user's vote."""
        ...

    # ── Community addons ──

    async def create_addon(self, addon: dict[str, Any]) -> dict[str, Any]:
        """Create a community addon (course, flashcard pack, etc.)."""
        ...

    async def get_addon_by_id(self, addon_id: str) -> dict[str, Any] | None:
        """Look up an addon by id."""
        ...

    async def list_addons(
        self,
        *,
        kind: str | None = None,
        language_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List addons with optional filters."""
        ...

    async def update_addon(self, addon_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """Merge patch into addon."""
        ...

    # ── Markdown file storage (for rich content, compatibility with React markdown editor) ──

    async def store_markdown(
        self,
        key: str,
        content: str,
        *,
        content_type: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Store markdown content by key. Key can be path-like (e.g. addons/abc123/readme).
        Returns stored record with id, key, created_at."""
        ...

    async def get_markdown(self, key: str) -> dict[str, Any] | None:
        """Retrieve markdown by key. Returns {key, content, content_type, metadata, updated_at}."""
        ...

    async def delete_markdown(self, key: str) -> bool:
        """Delete markdown by key. Returns True if deleted."""
        ...
