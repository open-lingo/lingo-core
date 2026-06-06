"""DynamoDB-backed tag repository — partial inert stub.

READ paths return empty so user-facing routes (``/tags``, deck reads
with tag join) don't 500 while the real impl is pending.

WRITE paths (create/update/delete/set_deck_tags) still raise so admin
actions surface as visible errors rather than silently appearing to
succeed while data is dropped.

Eventual layout (per ``lingo-infra/main.tf`` ``lingo_tags``):
  PK = SLUG#<slug>     SK = META               # canonical tag row
  PK = DECK#<deck_id>  SK = TAG#<slug>         # deck → tag mirror
  GSI1PK = TAG#<slug>  GSI1SK = DECK#<deck_id> # reverse lookup
"""

import logging
from typing import Any

logger = logging.getLogger("lingo.startup")


class DynamoTagRepository:
    def __init__(self, table_name: str, region: str) -> None:
        self._table_name = table_name
        self._region = region

    async def connect(self) -> None:
        logger.warning("DynamoTagRepository running in inert-stub mode — reads return empty, writes raise.")
        return None

    async def close(self) -> None:
        return None

    async def list_tags(self) -> list[dict[str, Any]]:
        return []

    async def get_tag(self, slug: str) -> dict[str, Any] | None:
        return None

    async def create_tag(
        self,
        slug: str,
        display_name: str,
        description: str | None = None,
        color: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError("DynamoTagRepository.create_tag")

    async def update_tag(
        self,
        slug: str,
        *,
        display_name: str | None = None,
        description: str | None = None,
        color: str | None = None,
    ) -> dict[str, Any] | None:
        raise NotImplementedError("DynamoTagRepository.update_tag")

    async def delete_tag(self, slug: str) -> bool:
        raise NotImplementedError("DynamoTagRepository.delete_tag")

    async def list_tags_for_deck(self, deck_id: str) -> list[str]:
        return []

    async def list_tags_for_decks(self, deck_ids: list[str]) -> dict[str, list[str]]:
        return {}

    async def list_decks_for_tag(self, slug: str) -> list[str]:
        return []

    async def set_deck_tags(self, deck_id: str, tag_slugs: list[str]) -> None:
        raise NotImplementedError("DynamoTagRepository.set_deck_tags")
