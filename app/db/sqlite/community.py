"""SQLite-backed community repository (stub).

Table: community_* (separate from users).
Real implementation to be added later. For now, use MockCommunityRepository.
"""

from typing import Any

# TODO: implement SQLite schema and operations
# CREATE TABLE forum_categories (...)
# CREATE TABLE forum_tags (...)
# CREATE TABLE forum_threads (...)
# CREATE TABLE forum_posts (...)
# CREATE TABLE forum_thread_tags (...)
# CREATE TABLE forum_content_links (...)
# CREATE TABLE forum_votes (...)
# CREATE TABLE community_addons (...)
# CREATE TABLE community_markdown (...)


class SqliteCommunityRepository:
    """SQLite community repo. Stub — use MockCommunityRepository until implemented."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def connect(self) -> None:
        """Connect to SQLite database."""
        raise NotImplementedError("SqliteCommunityRepository not yet implemented")

    async def close(self) -> None:
        """Close connection."""
        pass

    async def list_categories(self) -> list[dict[str, Any]]:
        raise NotImplementedError("Use MockCommunityRepository")

    async def get_category_by_id(self, category_id: str) -> dict[str, Any] | None:
        raise NotImplementedError("Use MockCommunityRepository")

    async def get_category_by_slug(self, slug: str) -> dict[str, Any] | None:
        raise NotImplementedError("Use MockCommunityRepository")

    async def list_tags(self) -> list[dict[str, Any]]:
        raise NotImplementedError("Use MockCommunityRepository")

    async def get_tag_by_id(self, tag_id: str) -> dict[str, Any] | None:
        raise NotImplementedError("Use MockCommunityRepository")

    async def create_tag(self, tag: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("Use MockCommunityRepository")

    async def create_thread(self, thread: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("Use MockCommunityRepository")

    async def get_thread_by_id(self, thread_id: str) -> dict[str, Any] | None:
        raise NotImplementedError("Use MockCommunityRepository")

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
        raise NotImplementedError("Use MockCommunityRepository")

    async def update_thread(self, thread_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("Use MockCommunityRepository")

    async def increment_thread_views(self, thread_id: str) -> None:
        raise NotImplementedError("Use MockCommunityRepository")

    async def create_post(self, post: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("Use MockCommunityRepository")

    async def get_post_by_id(self, post_id: str) -> dict[str, Any] | None:
        raise NotImplementedError("Use MockCommunityRepository")

    async def list_posts_by_thread(
        self,
        thread_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError("Use MockCommunityRepository")

    async def update_post(self, post_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("Use MockCommunityRepository")

    async def set_thread_tags(self, thread_id: str, tag_ids: list[str]) -> None:
        raise NotImplementedError("Use MockCommunityRepository")

    async def get_thread_tag_ids(self, thread_id: str) -> list[str]:
        raise NotImplementedError("Use MockCommunityRepository")

    async def add_content_link(
        self,
        thread_id: str,
        content_type: str,
        content_id: str,
        language_id: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError("Use MockCommunityRepository")

    async def list_content_links_by_thread(self, thread_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError("Use MockCommunityRepository")

    async def list_threads_by_content(
        self,
        content_type: str,
        content_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError("Use MockCommunityRepository")

    async def upsert_vote(
        self,
        user_id: str,
        target_type: str,
        target_id: str,
        value: int,
    ) -> None:
        raise NotImplementedError("Use MockCommunityRepository")

    async def get_user_vote(
        self,
        user_id: str,
        target_type: str,
        target_id: str,
    ) -> int | None:
        raise NotImplementedError("Use MockCommunityRepository")

    async def remove_vote(
        self,
        user_id: str,
        target_type: str,
        target_id: str,
    ) -> None:
        raise NotImplementedError("Use MockCommunityRepository")

    async def create_addon(self, addon: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("Use MockCommunityRepository")

    async def get_addon_by_id(self, addon_id: str) -> dict[str, Any] | None:
        raise NotImplementedError("Use MockCommunityRepository")

    async def list_addons(
        self,
        *,
        kind: str | None = None,
        language_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError("Use MockCommunityRepository")

    async def update_addon(self, addon_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("Use MockCommunityRepository")

    async def store_markdown(
        self,
        key: str,
        content: str,
        *,
        content_type: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError("Use MockCommunityRepository")

    async def get_markdown(self, key: str) -> dict[str, Any] | None:
        raise NotImplementedError("Use MockCommunityRepository")

    async def delete_markdown(self, key: str) -> bool:
        raise NotImplementedError("Use MockCommunityRepository")
