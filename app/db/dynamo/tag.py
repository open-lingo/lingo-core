"""DynamoDB-backed tag repository — stub.

SQLite is the working backend today; this stub raises ``NotImplementedError``
on every operation so the provider can wire it in without crashing at
startup. When it lands, the layout will likely be a single-table:

  PK = SLUG#<slug>           SK = META                # canonical tag row
  PK = DECK#<deck_id>        SK = TAG#<slug>          # deck → tag mirror
  GSI1PK = TAG#<slug>        GSI1SK = DECK#<deck_id>  # reverse: decks by tag

See lingo-infra/main.tf ``lingo_tags`` for the provisioned table.
"""

from typing import Any


class DynamoTagRepository:
    def __init__(self, table_name: str, region: str) -> None:
        self._table_name = table_name
        self._region = region

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def list_tags(self) -> list[dict[str, Any]]:
        raise NotImplementedError("DynamoTagRepository.list_tags")

    async def get_tag(self, slug: str) -> dict[str, Any] | None:
        raise NotImplementedError("DynamoTagRepository.get_tag")

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
        raise NotImplementedError("DynamoTagRepository.list_tags_for_deck")

    async def list_tags_for_decks(self, deck_ids: list[str]) -> dict[str, list[str]]:
        raise NotImplementedError("DynamoTagRepository.list_tags_for_decks")

    async def list_decks_for_tag(self, slug: str) -> list[str]:
        raise NotImplementedError("DynamoTagRepository.list_decks_for_tag")

    async def set_deck_tags(self, deck_id: str, tag_slugs: list[str]) -> None:
        raise NotImplementedError("DynamoTagRepository.set_deck_tags")
