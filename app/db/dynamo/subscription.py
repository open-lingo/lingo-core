"""DynamoDB-backed subscription repository.

Uses its own lingo_subscriptions table.
Keyed by our internal user UUID:

  PK = USER#<uuid>
  SK = SUB#<content_type>#<content_id>
"""

from datetime import UTC, datetime
from typing import Any

from app.db.dynamo._session import get_shared_resource

_SUB_SK_PREFIX = "SUB#"


def _sub_sk(content_type: str, content_id: str) -> str:
    return f"SUB#{content_type}#{content_id}"


def _item_to_sub(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "contentType": item["contentType"],
        "contentId": item["contentId"],
        "createdAt": item["createdAt"],
        "enabled": bool(item.get("enabled", True)),
        "newCardsPerDay": int(item.get("newCardsPerDay", 5)),
        "newCardOrder": item.get("newCardOrder", "ordered"),
    }


async def _paginate_query(table: Any, **kwargs: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    resp = await table.query(**kwargs)
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = await table.query(**kwargs, ExclusiveStartKey=resp["LastEvaluatedKey"])
        items.extend(resp.get("Items", []))
    return items


class DynamoSubscriptionRepository:
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

    async def add(self, user_id: str, content_type: str, content_id: str) -> None:
        now = datetime.now(UTC).isoformat()
        try:
            await self._table.put_item(
                Item={
                    "PK": f"USER#{user_id}",
                    "SK": _sub_sk(content_type, content_id),
                    "contentType": content_type,
                    "contentId": content_id,
                    "createdAt": now,
                    "enabled": True,
                    "newCardsPerDay": 5,
                    "newCardOrder": "ordered",
                },
                ConditionExpression="attribute_not_exists(PK)",
            )
        except Exception as exc:
            if "ConditionalCheckFailed" not in type(exc).__name__:
                raise

    async def remove(self, user_id: str, content_type: str, content_id: str) -> None:
        await self._table.delete_item(Key={"PK": f"USER#{user_id}", "SK": _sub_sk(content_type, content_id)})

    async def list(self, user_id: str, content_type: str | None = None) -> list[dict[str, Any]]:
        prefix = f"{_SUB_SK_PREFIX}{content_type}#" if content_type else _SUB_SK_PREFIX
        items = await _paginate_query(
            self._table,
            KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
            ExpressionAttributeValues={
                ":pk": f"USER#{user_id}",
                ":prefix": prefix,
            },
        )
        subs = [_item_to_sub(item) for item in items]
        subs.sort(key=lambda s: (s["contentType"], s["contentId"]))
        return subs

    async def update_settings(
        self,
        user_id: str,
        content_type: str,
        content_id: str,
        patch: dict[str, Any],
    ) -> bool:
        resp = await self._table.get_item(Key={"PK": f"USER#{user_id}", "SK": _sub_sk(content_type, content_id)})
        item = resp.get("Item")
        if not item:
            return False

        if "enabled" in patch:
            item["enabled"] = bool(patch["enabled"])
        if "newCardsPerDay" in patch:
            item["newCardsPerDay"] = int(patch["newCardsPerDay"])
        if "newCardOrder" in patch:
            item["newCardOrder"] = patch["newCardOrder"]

        await self._table.put_item(Item=item)
        return True

    async def has(self, user_id: str, content_type: str, content_id: str) -> bool:
        resp = await self._table.get_item(
            Key={"PK": f"USER#{user_id}", "SK": _sub_sk(content_type, content_id)},
            ProjectionExpression="PK",
        )
        return "Item" in resp
