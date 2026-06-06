"""DynamoDB-backed admin audit log — inert stub.

Until the real impl lands, ``append`` is a no-op (writes log line so
the moderation flow keeps working without persisting), and ``list``
returns an empty page. This prevents admin moderation actions from
500ing while the real Dynamo impl is pending.

Eventual schema:
  PK = ``AUDIT``
  SK = ``<at>#<id>``           (lexicographic = chronological)
  attrs = actor_id, action, target_id, target_kind, payload (Map), at
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("lingo.startup")
audit_log = logging.getLogger("lingo.audit")


class DynamoAuditRepository:
    def __init__(self, table_name: str, region: str) -> None:
        self._table_name = table_name
        self._region = region

    async def connect(self) -> None:
        logger.warning(
            "DynamoAuditRepository running in inert-stub mode — audit events log-only, not persisted."
        )
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
        # No persistence yet — emit to CloudWatch so audit trail isn't fully lost.
        audit_log.info(
            "audit actor=%s action=%s target_kind=%s target_id=%s",
            actor_id,
            action,
            target_kind,
            target_id,
        )
        return {
            "id": str(uuid.uuid4()),
            "actor_id": actor_id,
            "action": action,
            "target_id": target_id,
            "target_kind": target_kind,
            "payload": payload,
            "at": datetime.now(UTC).isoformat(),
        }

    async def list(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
        actor_id: str | None = None,
        target_kind: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        return ([], None)
