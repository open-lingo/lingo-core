"""DynamoDB-backed admin audit log.

Append-only log of admin actions, queried by ``/admin/audit``. Mirrors
``SqliteAuditRepository`` semantics so behaviour tests work across
backends.

Schema:
  Table  PK (S) = ``"AUDIT"`` (constant — single hot partition; admin
                              audit is low-volume, shard later if needed)
         SK (S) = ``"<at>#<id>"`` (lexicographic = chronological; query
                                   with ScanIndexForward=False for newest-first)
  GSI ``ActorIndex``       hash = ``actor_id`` (S), range = ``at`` (S)
  GSI ``TargetKindIndex``  hash = ``target_kind`` (S), range = ``at`` (S)

  Attributes: id, actor_id, action, target_id (sparse — omitted when None),
              target_kind, payload_json (S), at (S ISO timestamp).

Pagination: cursor is the base64-urlsafe-encoded JSON of LastEvaluatedKey,
matching the style ``DynamoUserRepository`` uses.
"""

from __future__ import annotations

import base64
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from app.db.dynamo._session import get_shared_resource

logger = logging.getLogger("lingo.startup")

_PARTITION = "AUDIT"


def _encode_cursor(lek: dict[str, Any] | None) -> str | None:
    if not lek:
        return None
    return base64.urlsafe_b64encode(json.dumps(lek, default=str).encode()).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> dict[str, Any] | None:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        return json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    except Exception:
        return None


def _item_to_row(item: dict[str, Any]) -> dict[str, Any]:
    raw_payload = item.get("payload_json") or "{}"
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        payload = {}
    return {
        "id": item.get("id"),
        "actor_id": item.get("actor_id", ""),
        "action": item.get("action"),
        # target_id is sparse — fall back to None to keep the protocol shape stable.
        "target_id": item.get("target_id"),
        "target_kind": item.get("target_kind"),
        "payload": payload,
        "at": item.get("at"),
    }


class DynamoAuditRepository:
    def __init__(self, table_name: str, region: str) -> None:
        self._table_name = table_name
        self._region = region
        self._table: Any = None

    async def connect(self) -> None:
        resource = await get_shared_resource(self._region)
        self._table = await resource.Table(self._table_name)

    async def close(self) -> None:
        # Shared resource closed via close_shared_resource(); no-op here.
        pass

    async def append(
        self,
        *,
        actor_id: str,
        action: str,
        target_id: str | None,
        target_kind: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entry_id = str(uuid.uuid4())
        at = datetime.now(UTC).isoformat()
        item: dict[str, Any] = {
            "PK": _PARTITION,
            "SK": f"{at}#{entry_id}",
            "id": entry_id,
            "actor_id": actor_id or "",
            "action": action,
            "target_kind": target_kind,
            "payload_json": json.dumps(payload or {}, default=str),
            "at": at,
        }
        # target_id is sparse — only store when present. Lets us add a
        # sparse GSI on it later without back-fill.
        if target_id is not None:
            item["target_id"] = target_id
        await self._table.put_item(Item=item)
        return {
            "id": entry_id,
            "actor_id": item["actor_id"],
            "action": action,
            "target_id": target_id,
            "target_kind": target_kind,
            "payload": payload or {},
            "at": at,
        }

    async def list(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
        actor_id: str | None = None,
        target_kind: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        # Pick the most-selective access pattern available.
        kwargs: dict[str, Any] = {
            "Limit": limit,
            "ScanIndexForward": False,  # newest first
        }
        start_key = _decode_cursor(cursor)
        if start_key:
            kwargs["ExclusiveStartKey"] = start_key

        if actor_id:
            kwargs["IndexName"] = "ActorIndex"
            kwargs["KeyConditionExpression"] = "actor_id = :aid"
            kwargs["ExpressionAttributeValues"] = {":aid": actor_id}
            if target_kind:
                kwargs["FilterExpression"] = "target_kind = :tk"
                kwargs["ExpressionAttributeValues"][":tk"] = target_kind
        elif target_kind:
            kwargs["IndexName"] = "TargetKindIndex"
            kwargs["KeyConditionExpression"] = "target_kind = :tk"
            kwargs["ExpressionAttributeValues"] = {":tk": target_kind}
        else:
            kwargs["KeyConditionExpression"] = "PK = :pk"
            kwargs["ExpressionAttributeValues"] = {":pk": _PARTITION}

        resp = await self._table.query(**kwargs)
        items = [_item_to_row(i) for i in resp.get("Items", [])]
        next_cursor = _encode_cursor(resp.get("LastEvaluatedKey"))
        return items, next_cursor
