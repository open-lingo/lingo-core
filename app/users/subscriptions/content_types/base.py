"""Base class for content-type-specific subscription logic."""

from abc import ABC, abstractmethod
from typing import Any


class BaseContentType(ABC):
    """Base for content type handlers. Subclasses can add validation, metadata, etc."""

    @property
    @abstractmethod
    def type_name(self) -> str:
        """The content type string (e.g. 'deck', 'addon')."""
        ...

    async def validate_subscription(self, content_id: str, context: dict[str, Any] | None = None) -> bool:
        """Validate that the content exists and can be subscribed to.
        Override in subclasses. Returns True if valid."""
        return True
