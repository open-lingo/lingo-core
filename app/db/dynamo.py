"""DynamoDB-backed repository for production.

Single-table design:
  PK = USER#<auth0_id>   SK = RECORD    → user record (id, username, status, …)
  PK = USER#<auth0_id>   SK = SETTINGS  → preferences blob

Username uniqueness is enforced via a GSI (GSI1PK = USERNAME#<username>)
on the RECORD item.  The ``id`` (UUID) is stored as an attribute; look-ups
by id would need a separate GSI if required at scale.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import aioboto3

_RECORD_SK = "RECORD"
_SETTINGS_SK = "SETTINGS"


class DynamoUserRepository:
    def __init__(self, table_name: str, region: str) -> None:
        self._table_name = table_name
        self._region = region
        self._session = aioboto3.Session()
        self._table: Any = None
        self._resource_ctx: Any = None

    async def connect(self) -> None:
        self._resource_ctx = self._session.resource("dynamodb", region_name=self._region)
        resource = await self._resource_ctx.__aenter__()
        self._table = await resource.Table(self._table_name)

    async def close(self) -> None:
        if self._resource_ctx:
            await self._resource_ctx.__aexit__(None, None, None)

    def _pk(self, auth0_id: str) -> str:
        return f"USER#{auth0_id}"

    def _strip_keys(self, item: dict[str, Any]) -> dict[str, Any]:
        """Remove DynamoDB key attributes from the returned dict."""
        data = dict(item)
        for k in ("PK", "SK", "GSI1PK", "GSI1SK"):
            data.pop(k, None)
        return data

    # -- User record --

    async def create_user(self, user: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        auth0_id = user["auth0_id"]
        row = {
            "id": str(uuid.uuid4()),
            "auth0_id": auth0_id,
            "username": user["username"],
            "display_name": user["display_name"],
            "profile_picture_key": user.get("profile_picture_key"),
            "status": user.get("status", "active"),
            "created_at": now,
            "updated_at": now,
        }
        item = {
            "PK": self._pk(auth0_id),
            "SK": _RECORD_SK,
            "GSI1PK": f"USERNAME#{row['username']}",
            "GSI1SK": _RECORD_SK,
            **row,
        }
        await self._table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(PK)",
        )
        return row

    async def get_user_by_auth0_id(self, auth0_id: str) -> dict[str, Any] | None:
        resp = await self._table.get_item(
            Key={"PK": self._pk(auth0_id), "SK": _RECORD_SK}
        )
        item = resp.get("Item")
        return self._strip_keys(item) if item else None

    async def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        # Scan is fine for low-volume lookups; add a GSI on `id` if this
        # becomes a hot path.
        resp = await self._table.scan(
            FilterExpression="id = :uid AND SK = :sk",
            ExpressionAttributeValues={":uid": user_id, ":sk": _RECORD_SK},
            Limit=1,
        )
        items = resp.get("Items", [])
        return self._strip_keys(items[0]) if items else None

    async def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        resp = await self._table.query(
            IndexName="GSI1",
            KeyConditionExpression="GSI1PK = :pk AND GSI1SK = :sk",
            ExpressionAttributeValues={
                ":pk": f"USERNAME#{username}",
                ":sk": _RECORD_SK,
            },
            Limit=1,
        )
        items = resp.get("Items", [])
        return self._strip_keys(items[0]) if items else None

    async def update_user(self, auth0_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        current = await self.get_user_by_auth0_id(auth0_id)
        if current is None:
            raise LookupError(f"No user with auth0_id={auth0_id!r}")

        current.update(patch)
        current["updated_at"] = datetime.now(UTC).isoformat()

        item = {
            "PK": self._pk(auth0_id),
            "SK": _RECORD_SK,
            "GSI1PK": f"USERNAME#{current['username']}",
            "GSI1SK": _RECORD_SK,
            **current,
        }
        await self._table.put_item(Item=item)
        return current

    # -- User settings --

    async def get_settings(self, auth0_id: str) -> dict[str, Any] | None:
        resp = await self._table.get_item(
            Key={"PK": self._pk(auth0_id), "SK": _SETTINGS_SK}
        )
        item = resp.get("Item")
        return self._strip_keys(item) if item else None

    async def update_settings(self, auth0_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        current = await self.get_settings(auth0_id) or {}
        current.update(patch)
        item = {"PK": self._pk(auth0_id), "SK": _SETTINGS_SK, **current}
        await self._table.put_item(Item=item)
        return current
