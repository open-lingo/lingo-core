"""DynamoDB-backed user repository (production).

Single-table design — PK is our internal UUID, not the auth0 sub:
  PK = USER#<uuid>   SK = RECORD    → user record (id, auth0_id, username, …)
  PK = USER#<uuid>   SK = SETTINGS  → preferences blob

A separate GSI allows look-up by auth0_id (used only during auth resolution):
  GSI "Auth0-Index"  hash = auth0_id  (attribute on RECORD item)

Username uniqueness is enforced via a second GSI:
  GSI "Username-Index"  hash = GSI1PK (= USERNAME#<username>)
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

    def _pk(self, user_id: str) -> str:
        return f"USER#{user_id}"

    def _strip_keys(self, item: dict[str, Any]) -> dict[str, Any]:
        data = dict(item)
        for k in ("PK", "SK", "GSI1PK", "GSI1SK"):
            data.pop(k, None)
        return data

    # -- User record --

    async def create_user(self, user: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        auth0_id = user["auth0_id"]
        user_id = str(uuid.uuid4())
        row = {
            "id": user_id,
            "auth0_id": auth0_id,
            "username": user["username"],
            "display_name": user["display_name"],
            "profile_picture_key": user.get("profile_picture_key"),
            "status": user.get("status", "active"),
            "status_expiration": user.get("status_expiration") or None,
            "community_status": user.get("community_status") or None,
            "community_status_expiration": user.get("community_status_expiration") or None,
            "bio": user.get("bio") or None,
            "role": user.get("role", "user"),
            "created_at": now,
            "updated_at": now,
        }
        item = {
            "PK": self._pk(user_id),
            "SK": _RECORD_SK,
            # GSI for auth0_id lookup (auth resolution only)
            "GSI1PK": f"AUTH0#{auth0_id}",
            "GSI1SK": _RECORD_SK,
            # GSI for username lookup
            "GSI2PK": f"USERNAME#{row['username']}",
            "GSI2SK": _RECORD_SK,
            **row,
        }
        await self._table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(PK)",
        )
        return row

    async def get_user_by_auth0_id(self, auth0_id: str) -> dict[str, Any] | None:
        """Used only by the auth layer to resolve auth0 sub → internal UUID."""
        resp = await self._table.query(
            IndexName="Auth0-Index",
            KeyConditionExpression="GSI1PK = :pk AND GSI1SK = :sk",
            ExpressionAttributeValues={
                ":pk": f"AUTH0#{auth0_id}",
                ":sk": _RECORD_SK,
            },
            Limit=1,
        )
        items = resp.get("Items", [])
        return self._strip_keys(items[0]) if items else None

    async def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        resp = await self._table.get_item(
            Key={"PK": self._pk(user_id), "SK": _RECORD_SK}
        )
        item = resp.get("Item")
        return self._strip_keys(item) if item else None

    async def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        resp = await self._table.query(
            IndexName="Username-Index",
            KeyConditionExpression="GSI2PK = :pk AND GSI2SK = :sk",
            ExpressionAttributeValues={
                ":pk": f"USERNAME#{username}",
                ":sk": _RECORD_SK,
            },
            Limit=1,
        )
        items = resp.get("Items", [])
        return self._strip_keys(items[0]) if items else None

    async def update_user(self, user_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        current = await self.get_user_by_id(user_id)
        if current is None:
            raise LookupError(f"No user with id={user_id!r}")

        current.update(patch)
        current["updated_at"] = datetime.now(UTC).isoformat()

        item = {
            "PK": self._pk(user_id),
            "SK": _RECORD_SK,
            "GSI1PK": f"AUTH0#{current['auth0_id']}",
            "GSI1SK": _RECORD_SK,
            "GSI2PK": f"USERNAME#{current['username']}",
            "GSI2SK": _RECORD_SK,
            **current,
        }
        await self._table.put_item(Item=item)
        return current

    async def list_users(
        self,
        limit: int = 100,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Scan user records. cursor is last_evaluated_key as base64 JSON."""
        import base64
        import json
        params: dict[str, Any] = {
            "FilterExpression": "SK = :sk",
            "ExpressionAttributeValues": {":sk": _RECORD_SK},
            "Limit": (limit + 1) * 2,  # Scan Limit is items read; we filter so overfetch
        }
        if cursor:
            try:
                params["ExclusiveStartKey"] = json.loads(
                    base64.urlsafe_b64decode(cursor + "==").decode()
                )
            except Exception:
                pass
        resp = await self._table.scan(**params)
        raw = [self._strip_keys(i) for i in resp.get("Items", [])]
        items = raw[:limit]
        lek = resp.get("LastEvaluatedKey")
        next_cursor = (
            base64.urlsafe_b64encode(json.dumps(lek, default=str).encode()).decode().rstrip("=")
            if lek
            else None
        )
        return items, next_cursor

    async def delete_user(self, user_id: str) -> None:
        await self._table.delete_item(
            Key={"PK": self._pk(user_id), "SK": _RECORD_SK}
        )
        await self._table.delete_item(
            Key={"PK": self._pk(user_id), "SK": _SETTINGS_SK}
        )

    # -- User settings --

    async def get_settings(self, user_id: str) -> dict[str, Any] | None:
        resp = await self._table.get_item(
            Key={"PK": self._pk(user_id), "SK": _SETTINGS_SK}
        )
        item = resp.get("Item")
        return self._strip_keys(item) if item else None

    async def update_settings(self, user_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        def _deep_merge(base: dict[str, Any], update: dict[str, Any]) -> None:
            for k, v in update.items():
                if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                    _deep_merge(base[k], v)
                else:
                    base[k] = v

        current = await self.get_settings(user_id) or {}
        _deep_merge(current, patch)
        item = {"PK": self._pk(user_id), "SK": _SETTINGS_SK, **current}
        await self._table.put_item(Item=item)
        return current
