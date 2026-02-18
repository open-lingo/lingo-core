"""DynamoDB-backed repository for production.

Uses a single-table design with ``PK = USER#<sub>`` and
``SK = SETTINGS | PROFILE`` so all user data lives in one table.

Requires ``aioboto3`` and valid AWS credentials in the environment.
"""

from typing import Any

import aioboto3


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

    # -- UserRepository protocol --

    async def get_settings(self, user_id: str) -> dict[str, Any] | None:
        resp = await self._table.get_item(
            Key={"PK": self._pk(user_id), "SK": "SETTINGS"}
        )
        item = resp.get("Item")
        if not item:
            return None
        data = dict(item)
        data.pop("PK", None)
        data.pop("SK", None)
        return data

    async def update_settings(self, user_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        current = await self.get_settings(user_id) or {}
        current.update(patch)
        item = {"PK": self._pk(user_id), "SK": "SETTINGS", **current}
        await self._table.put_item(Item=item)
        return current

    async def get_profile(self, user_id: str) -> dict[str, Any] | None:
        resp = await self._table.get_item(
            Key={"PK": self._pk(user_id), "SK": "PROFILE"}
        )
        item = resp.get("Item")
        if not item:
            return None
        data = dict(item)
        data.pop("PK", None)
        data.pop("SK", None)
        return data

    async def upsert_profile(self, user_id: str, data: dict[str, Any]) -> dict[str, Any]:
        item = {"PK": self._pk(user_id), "SK": "PROFILE", **data}
        await self._table.put_item(Item=item)
        return data
