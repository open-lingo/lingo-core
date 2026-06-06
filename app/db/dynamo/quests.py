"""DynamoDB-backed quest repository — inert stub.

The full DynamoDB-backed implementation isn't written yet. Until it
lands, these methods return empty/no-op results instead of raising
``NotImplementedError`` — that way `/quests` endpoints respond 200
with "no active quests" rather than 500ing every request. The FE then
shows an empty state instead of an error banner.

When the real impl lands, the table will likely use:
  PK = ``USER#<user_id>``
  SK = ``QUEST#<quest_id>``
  attrs = the same shape returned by ``SqliteQuestRepository._row_to_quest``.
"""

import logging
from typing import Any

logger = logging.getLogger("lingo.startup")


class DynamoQuestRepository:
    def __init__(self, table_name: str, region: str) -> None:
        self._table_name = table_name
        self._region = region

    async def connect(self) -> None:
        logger.warning("DynamoQuestRepository running in inert-stub mode — quests will not persist.")
        return None

    async def close(self) -> None:
        return None

    async def list_quests(self, user_id: str) -> list[dict[str, Any]]:
        return []

    async def get_quest(self, user_id: str, quest_id: str) -> dict[str, Any] | None:
        return None

    async def put_quest(self, quest: dict[str, Any]) -> dict[str, Any]:
        return quest

    async def update_progress(self, user_id: str, quest_id: str, delta: int) -> dict[str, Any] | None:
        return None

    async def claim(self, user_id: str, quest_id: str) -> dict[str, Any] | None:
        return None

    async def delete_user_quests(self, user_id: str, types: list[str] | None = None) -> int:
        return 0
