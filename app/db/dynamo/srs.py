"""DynamoDB-backed SRS repository — FSRS-6 with modality split.

Single-table design — keyed by our internal user UUID:
  PK = USER#<uuid>   SK = CARD#<card_id>  → one item per (user, card)

GSI (DueDate-Index):
  hash  = user_id  (plain UUID attribute)
  range = dueDate  (min of both modalities, YYYY-MM-DD, sorts lexicographically)

Full FSRS-6 modal state is stored as a nested map attribute (state_map).
The top-level dueDate attribute is computed as min(recognition.dueDate,
production.dueDate) so the GSI range query returns cards due in either modality.

Numeric note: DynamoDB returns all numbers as Decimal. The state is stored
as a JSON string (state_json attribute) to avoid Decimal conversion hassles
on nested structures. The dueDate attribute remains a plain string for the GSI.
"""

import asyncio
import json
from typing import Any

from app.db.dynamo._session import get_shared_resource

_CARD_SK_PREFIX = "CARD#"


def _min_due(state: dict[str, Any]) -> str:
    r = state.get("recognition", {}).get("dueDate", "")
    p = state.get("production", {}).get("dueDate", "")
    if not r:
        return p
    if not p:
        return r
    return min(r, p)


def _max_last_review(state: dict[str, Any]) -> str:
    r = state.get("recognition", {}).get("lastReviewDate", "")
    p = state.get("production", {}).get("lastReviewDate", "")
    return max(r, p)


def _item_to_state(item: dict[str, Any]) -> dict[str, Any]:
    if "state_json" in item:
        return json.loads(item["state_json"])
    # Legacy SM-2 item — return None so caller can skip.
    return {}


async def _paginate_query(table: Any, **kwargs: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    resp = await table.query(**kwargs)
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = await table.query(**kwargs, ExclusiveStartKey=resp["LastEvaluatedKey"])
        items.extend(resp.get("Items", []))
    return items


class DynamoSRSRepository:
    """DynamoDB-backed SRS repository (FSRS-6 modal).

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
        self._table: Any = None

    async def connect(self) -> None:
        resource = await get_shared_resource(self._region)
        self._table = await resource.Table(self._table_name)

    async def close(self) -> None:
        # Shared resource closed via close_shared_resource(); no-op here.
        pass

    async def get_all(self, user_id: str) -> dict[str, dict[str, Any]]:
        items = await _paginate_query(
            self._table,
            KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
            ExpressionAttributeValues={
                ":pk": f"USER#{user_id}",
                ":prefix": _CARD_SK_PREFIX,
            },
        )
        result: dict[str, dict[str, Any]] = {}
        for item in items:
            state = _item_to_state(item)
            if state:
                result[item["SK"][len(_CARD_SK_PREFIX) :]] = state
        return result

    async def get_due_cards(self, user_id: str, on_or_before: str) -> dict[str, dict[str, Any]]:
        items = await _paginate_query(
            self._table,
            IndexName="DueDate-Index",
            KeyConditionExpression="user_id = :uid AND dueDate <= :date",
            ExpressionAttributeValues={
                ":uid": user_id,
                ":date": on_or_before,
            },
        )
        result: dict[str, dict[str, Any]] = {}
        for item in items:
            state = _item_to_state(item)
            if state:
                result[item["SK"][len(_CARD_SK_PREFIX) :]] = state
        return result

    async def get_card(self, user_id: str, card_id: str) -> dict[str, Any] | None:
        resp = await self._table.get_item(Key={"PK": f"USER#{user_id}", "SK": f"{_CARD_SK_PREFIX}{card_id}"})
        item = resp.get("Item")
        if not item:
            return None
        state = _item_to_state(item)
        return state if state else None

    async def upsert_cards(self, user_id: str, cards: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
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

            incoming_review = _max_last_review(incoming)
            existing_review = _max_last_review(existing) if existing else ""
            core_win = existing and existing_review >= incoming_review

            bury_changed = existing and "buriedUntil" in incoming and incoming.get("buriedUntil") != existing.get("buriedUntil")

            if core_win and not bury_changed:
                result[card_id] = existing
                continue

            if core_win and bury_changed and existing:
                incoming = {**existing, "buriedUntil": incoming.get("buriedUntil")}

            due = _min_due(incoming)
            item: dict[str, Any] = {
                "PK": f"USER#{user_id}",
                "SK": f"{_CARD_SK_PREFIX}{card_id}",
                "user_id": user_id,
                "dueDate": due,
                "state_json": json.dumps(incoming, ensure_ascii=False),
            }
            await self._table.put_item(Item=item)
            result[card_id] = incoming

        return result

    async def delete_cards(self, user_id: str, card_ids: list[str]) -> int:
        for card_id in card_ids:
            await self._table.delete_item(Key={"PK": f"USER#{user_id}", "SK": f"{_CARD_SK_PREFIX}{card_id}"})
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
