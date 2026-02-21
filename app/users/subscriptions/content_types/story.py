"""Story subscription handler."""

from app.users.subscriptions.content_types.base import BaseContentType


class StoryContentType(BaseContentType):
    """Handler for story subscriptions."""

    @property
    def type_name(self) -> str:
        return "story"
