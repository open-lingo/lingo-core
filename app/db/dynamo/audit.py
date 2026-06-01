"""DynamoDB-backed admin audit log — stub.

The SQLite implementation is the only working backend today; this stub
raises ``NotImplementedError`` on append/list so the provider can wire
it in without crashing at startup.

Schema sketch for the eventual Dynamo impl:

  PK = ``AUDIT``
  SK = ``<at>#<id>``           (lexicographic = chronological)
  attrs = actor_id, action, target_id, target_kind, payload (Map), at
"""

from typing import Any


class DynamoAuditRepository:
    def __init__(self, table_name: str, region: str) -> None:
        self._table_name = table_name
        self._region = region

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def append(
        self,
        *,
        actor_id: str,
        action: str,
        target_id: str | None,
        target_kind: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError("DynamoAuditRepository.append")

    async def list(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
        actor_id: str | None = None,
        target_kind: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        raise NotImplementedError("DynamoAuditRepository.list")
