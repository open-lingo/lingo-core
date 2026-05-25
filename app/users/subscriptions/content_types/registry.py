"""Registry of content type handlers."""

from typing import Any

from app.users.subscriptions.content_types.addon import AddonContentType
from app.users.subscriptions.content_types.base import BaseContentType
from app.users.subscriptions.content_types.deck import DeckContentType
from app.users.subscriptions.content_types.story import StoryContentType
from app.users.subscriptions.types import ContentType


def get_content_type_handler(
    content_type: str, context: dict[str, Any] | None = None
) -> BaseContentType:
    """Return the handler for a content type. Context can inject deps (e.g. deck_repo)."""
    context = context or {}
    if content_type == ContentType.DECK:
        return DeckContentType(deck_repo=context.get("deck_repo"))
    if content_type == ContentType.ADDON:
        return AddonContentType()
    if content_type == ContentType.STORY:
        return StoryContentType(story_repo=context.get("story_repo"))
    raise ValueError(f"Unknown content type: {content_type!r}")
