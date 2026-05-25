"""DynamoDB-backed quest repository — stub.

The SQLite implementation is the only working backend today; this stub
raises ``NotImplementedError`` on every operation so the provider can wire
it in without crashing at startup.

When this lands, the table schema will likely be:

  PK = ``USER#<user_id>``
  SK = ``QUEST#<quest_id>``
  attrs = the same shape returned by ``SqliteQuestRepository._row_to_quest``.
"""

from typing import Any


class DynamoQuestRepository:
    def __init__(self, table_name: str, region: str) -> None:
        self._table_name = table_name
        self._region = region

    async def connect(self) -> None:
        # No-op so provider init can succeed; methods raise on use.
        return None

    async def close(self) -> None:
        return None

    async def list_quests(self, user_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError("DynamoQuestRepository.list_quests")

    async def get_quest(self, user_id: str, quest_id: str) -> dict[str, Any] | None:
        raise NotImplementedError("DynamoQuestRepository.get_quest")

    async def put_quest(self, quest: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("DynamoQuestRepository.put_quest")

    async def update_progress(
        self, user_id: str, quest_id: str, delta: int
    ) -> dict[str, Any] | None:
        raise NotImplementedError("DynamoQuestRepository.update_progress")

    async def claim(self, user_id: str, quest_id: str) -> dict[str, Any] | None:
        raise NotImplementedError("DynamoQuestRepository.claim")

    async def delete_user_quests(
        self, user_id: str, types: list[str] | None = None
    ) -> int:
        raise NotImplementedError("DynamoQuestRepository.delete_user_quests")
