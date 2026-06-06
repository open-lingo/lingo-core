"""DynamoDB-backed platform settings repository.

Tiny key/value store backing admin-tunable configuration (XP economy
today, more knobs later). Mirrors ``SqlitePlatformSettingsRepository``.

Schema:
  Table  PK (S) = ``key`` (e.g. ``"xp_economy"``)
         SK (S) = ``"META"`` (constant — leaves room to add per-key sub-items later)
  Attribute ``value_json`` (S) — JSON-serialized dict.

Why JSON-encode instead of using Dynamo Map types: Map attributes don't
preserve key order, and the FE relies on dict iteration order being
insertion-order for the XP-economy form.
"""

import json
from datetime import UTC, datetime
from typing import Any

from app.db.dynamo._session import get_shared_resource

_META_SK = "META"


class DynamoPlatformSettingsRepository:
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

    async def get(self, key: str) -> dict[str, Any] | None:
        resp = await self._table.get_item(Key={"key": key, "SK": _META_SK})
        item = resp.get("Item")
        if not item:
            return None
        raw = item.get("value_json")
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    async def put(self, key: str, value: dict[str, Any]) -> dict[str, Any]:
        await self._table.put_item(
            Item={
                "key": key,
                "SK": _META_SK,
                "value_json": json.dumps(value, ensure_ascii=False),
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )
        return dict(value)
