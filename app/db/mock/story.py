"""In-memory mock story repository.

Used in production until ``DynamoStoryRepository`` lands. Provides the
full StoryRepository protocol surface (list / get / create / update /
delete) backed by a process-local dict so admin moderation + user
browse-by-language don't 503.

Data resets on Lambda cold start. That's the same trade-off
``MockCommunityRepository`` accepts, and is documented in
``ARCHITECTURE_REVIEW.md``. Stories created via this impl will
disappear when Lambda recycles — that's an explicit deferred concern
captured by the inert ``DynamoStoryRepository`` issue.
"""

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat()


class MockStoryRepository:
    """In-memory implementation of StoryRepository."""

    def __init__(self) -> None:
        self._stories: dict[str, dict[str, Any]] = {}

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def list_stories(
        self,
        author_id: str | None = None,
        language_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        out = []
        for s in self._stories.values():
            if author_id is not None and s.get("author_id") != author_id:
                continue
            if language_id is not None and s.get("language_id") != language_id:
                continue
            if status is not None and s.get("status") != status:
                continue
            out.append(deepcopy(s))
        # Newest first by created_at; missing timestamps sort last.
        out.sort(key=lambda s: s.get("created_at") or "", reverse=True)
        return out

    async def get_story(self, story_id: str) -> dict[str, Any] | None:
        s = self._stories.get(story_id)
        return deepcopy(s) if s else None

    async def create_story(self, story_id: str, data: dict[str, Any]) -> None:
        now = _now()
        row = {
            "id": story_id,
            "created_at": now,
            "updated_at": now,
            **data,
        }
        self._stories[story_id] = row

    async def update_story(self, story_id: str, data: dict[str, Any]) -> None:
        existing = self._stories.get(story_id)
        if not existing:
            return
        existing.update(data)
        existing["updated_at"] = _now()

    async def delete_story(self, story_id: str) -> None:
        self._stories.pop(story_id, None)
