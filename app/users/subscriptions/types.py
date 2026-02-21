"""Subscription types and content type enum."""

from enum import StrEnum


class ContentType(StrEnum):
    """Content type for subscriptions. Add new types here as needed."""

    DECK = "deck"
    ADDON = "addon"
    STORY = "story"


class Subscription:
    """A user's subscription to a piece of content."""

    def __init__(
        self,
        auth0_id: str,
        content_type: str,
        content_id: str,
        created_at: str | None = None,
    ):
        self.auth0_id = auth0_id
        self.content_type = content_type
        self.content_id = content_id
        self.created_at = created_at

    def to_dict(self) -> dict:
        return {
            "contentType": self.content_type,
            "contentId": self.content_id,
            "createdAt": self.created_at,
        }
