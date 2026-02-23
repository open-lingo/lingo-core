"""DynamoDB-backed SRS repository.

Single-table design — keyed by our internal user UUID:
  PK = USER#<uuid>   SK = CARD#<card_id>  → one item per (user, card)

GSI (DueDate-Index):
  hash  = user_id  (plain UUID attribute)
  range = dueDate  (YYYY-MM-DD string, sorts lexicographically)

  Used by get_due_cards() for efficient range queries.

Numeric note: DynamoDB returns all numbers as Decimal.  We convert:
  - easeFactor  (float) → Decimal on write, float on read
  - interval / repetitions (int) → int on read  (Decimal → int is safe)
"""

import asyncio
import json
from decimal import Decimal
from typing import Any

import aioboto3

_CARD_SK_PREFIX = "CARD#"


def _to_decimal(val: float | int) -> Decimal:
    return Decimal(str(val))


def _item_to_state(item: dict[str, Any]) -> dict[str, Any]:
    state: dict[str, Any] = {
        "easeFactor": float(item["easeFactor"]),
        "interval": int(item["interval"]),
        "dueDate": item["dueDate"],
        "repetitions": int(item["repetitions"]),
        "lastReviewDate": item["lastReviewDate"],
    }
    if "lastSyncedAt" in item:
        state["lastSyncedAt"] = item["lastSyncedAt"]
    if "buriedUntil" in item and item["buriedUntil"]:
        state["buriedUntil"] = item["buriedUntil"]
    return state


def _state_to_item(
    user_id: str, card_id: str, state: dict[str, Any]
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "PK": f"USER#{user_id}",
        "SK": f"{_CARD_SK_PREFIX}{card_id}",
        "user_id": user_id,  # plain attribute used as GSI hash key
        "easeFactor": _to_decimal(state.get("easeFactor", 2.5)),
        "interval": int(state.get("interval", 0)),
        "dueDate": state.get("dueDate", ""),
        "repetitions": int(state.get("repetitions", 0)),
        "lastReviewDate": state.get("lastReviewDate", ""),
    }
    if state.get("lastSyncedAt"):
        item["lastSyncedAt"] = state["lastSyncedAt"]
    if state.get("buriedUntil"):
        item["buriedUntil"] = state["buriedUntil"]
    return item


async def _paginate_query(table: Any, **kwargs: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    resp = await table.query(**kwargs)
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = await table.query(**kwargs, ExclusiveStartKey=resp["LastEvaluatedKey"])
        items.extend(resp.get("Items", []))
    return items


class DynamoSRSRepository:
    """DynamoDB-backed SRS repository.

    Required table / GSI:
      Table  PK (S) + SK (S)
      GSI    "DueDate-Index"  hash=user_id (S)  range=dueDate (S)

    Create this table (AWS CLI example):

      aws dynamodb create-table \\
        --table-name lingo_srs \\
        --attribute-definitions \\
            AttributeName=PK,AttributeType=S \\
            AttributeName=SK,AttributeType=S \\
            AttributeName=user_id,AttributeType=S \\
            AttributeName=dueDate,AttributeType=S \\
        --key-schema AttributeName=PK,KeyType=HASH AttributeName=SK,KeyType=RANGE \\
        --global-secondary-indexes '[{
            "IndexName":"DueDate-Index",
            "KeySchema":[
                {"AttributeName":"user_id","KeyType":"HASH"},
                {"AttributeName":"dueDate","KeyType":"RANGE"}
            ],
            "Projection":{"ProjectionType":"ALL"}
        }]' \\
        --billing-mode PAY_PER_REQUEST
    """

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

    async def get_all(self, user_id: str) -> dict[str, dict[str, Any]]:
        items = await _paginate_query(
            self._table,
            KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
            ExpressionAttributeValues={
                ":pk": f"USER#{user_id}",
                ":prefix": _CARD_SK_PREFIX,
            },
        )
        return {item["SK"][len(_CARD_SK_PREFIX):]: _item_to_state(item) for item in items}

    async def get_due_cards(
        self, user_id: str, on_or_before: str
    ) -> dict[str, dict[str, Any]]:
        items = await _paginate_query(
            self._table,
            IndexName="DueDate-Index",
            KeyConditionExpression="user_id = :uid AND dueDate <= :date",
            ExpressionAttributeValues={
                ":uid": user_id,
                ":date": on_or_before,
            },
        )
        return {item["SK"][len(_CARD_SK_PREFIX):]: _item_to_state(item) for item in items}

    async def get_card(self, user_id: str, card_id: str) -> dict[str, Any] | None:
        resp = await self._table.get_item(
            Key={"PK": f"USER#{user_id}", "SK": f"{_CARD_SK_PREFIX}{card_id}"}
        )
        item = resp.get("Item")
        return _item_to_state(item) if item else None

    async def upsert_cards(
        self, user_id: str, cards: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        card_ids = list(cards.keys())
        existing_list = await asyncio.gather(
            *[self.get_card(user_id, cid) for cid in card_ids],
            return_exceptions=True,
        )
        existing_map: dict[str, dict[str, Any]] = {}
        for cid, state in zip(card_ids, existing_list):
            if isinstance(state, Exception) or state is None:
                continue
            existing_map[cid] = state

        result: dict[str, dict[str, Any]] = {}
        for card_id, incoming in cards.items():
            existing = existing_map.get(card_id)

            core_win = (
                existing
                and existing.get("lastReviewDate", "") >= incoming.get("lastReviewDate", "")
            )
            bury_changed = (
                existing
                and "buriedUntil" in incoming
                and incoming.get("buriedUntil") != existing.get("buriedUntil")
            )

            if core_win and not bury_changed:
                result[card_id] = existing
                continue

            if core_win and bury_changed and existing:
                incoming = {**existing, "buriedUntil": incoming.get("buriedUntil")}

            item = _state_to_item(user_id, card_id, incoming)
            await self._table.put_item(Item=item)
            result[card_id] = incoming

        return result

    async def delete_cards(self, user_id: str, card_ids: list[str]) -> int:
        for card_id in card_ids:
            await self._table.delete_item(
                Key={"PK": f"USER#{user_id}", "SK": f"{_CARD_SK_PREFIX}{card_id}"}
            )
        return len(card_ids)

    async def clear_all(self, user_id: str) -> int:
        items = await _paginate_query(
            self._table,
            KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
            ExpressionAttributeValues={
                ":pk": f"USER#{user_id}",
                ":prefix": _CARD_SK_PREFIX,
            },
            ProjectionExpression="PK, SK",
        )
        async with self._table.batch_writer() as batch:
            for item in items:
                await batch.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})
        return len(items)
