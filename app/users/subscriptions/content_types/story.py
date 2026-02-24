"""Story subscription handler."""

from typing import TYPE_CHECKING, Any

from app.users.subscriptions.content_types.base import BaseContentType

if TYPE_CHECKING:
    from app.db.protocols import StoryRepository


class StoryContentType(BaseContentType):
    """Handler for story subscriptions. Validates story exists."""

    def __init__(self, story_repo: "StoryRepository | None" = None):
        self._story_repo = story_repo

    @property
    def type_name(self) -> str:
        return "story"

    async def validate_subscription(
        self, content_id: str, context: dict[str, Any] | None = None
    ) -> bool:
        repo = self._story_repo or (context or {}).get("story_repo")
        if repo is None:
            return True
        story = await repo.get_story(content_id)
        return story is not None and story.get("status") == "published"
