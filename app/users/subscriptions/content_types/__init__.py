"""Content-type-specific subscription handlers.

Each content type can evolve independently (validation, metadata, etc.).
"""

from app.users.subscriptions.content_types.base import BaseContentType
from app.users.subscriptions.content_types.deck import DeckContentType
from app.users.subscriptions.content_types.registry import get_content_type_handler

__all__ = ["BaseContentType", "DeckContentType", "get_content_type_handler"]
