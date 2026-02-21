"""Addon subscription handler."""

from app.users.subscriptions.content_types.base import BaseContentType


class AddonContentType(BaseContentType):
    """Handler for addon subscriptions."""

    @property
    def type_name(self) -> str:
        return "addon"
