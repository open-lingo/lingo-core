"""DynamoDB-backed SRS repository.

Single-table design — keyed by our internal user UUID:
  PK = USER#<uuid>   SK = CARD#<card_id>  → one item per (user, card)

GSI (DueDate-Index):
  hash  = user_id  (plain UUID attribute)
  range = dueDate  (YYYY-MM-DD string, sorts lexicographically)

  Used by get_due_cards() for efficient range queries.

Card state is stored as an opaque ``payload`` map. We extract two
top-level fields onto the item so they can be queried/indexed:
  - ``dueDate``         — earliest of modal dueDates; populates DueDate-Index
  - ``lastReviewedAt``  — ISO timestamp; lex-sortable LWW key
"""

import asyncio
from typing import Any

import aioboto3

from app.shared.utils import earliest_due_date

_CARD_SK_PREFIX = "CARD#"


def _item_to_state(item: dict[str, Any]) -> dict[str, Any]:
    payload = item.get("payload")
    if isinstance(payload, dict):
        return dict(payload)
    return {}


def _state_to_item(
    user_id: str, card_id: str, state: dict[str, Any]
) -> dict[str, Any]:
    return {
        "PK": f"USER#{user_id}",
        "SK": f"{_CARD_SK_PREFIX}{card_id}",
        "user_id": user_id,
        "dueDate": earliest_due_date(state),
        "lastReviewedAt": str(state.get("lastReviewedAt") or ""),
        "payload": state,
    }


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

    async def _get_raw_item(
        self, user_id: str, card_id: str
    ) -> dict[str, Any] | None:
        resp = await self._table.get_item(
            Key={"PK": f"USER#{user_id}", "SK": f"{_CARD_SK_PREFIX}{card_id}"}
        )
        return resp.get("Item")

    async def upsert_cards(
        self, user_id: str, cards: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        card_ids = list(cards.keys())
        existing_list = await asyncio.gather(
            *[self._get_raw_item(user_id, cid) for cid in card_ids],
            return_exceptions=True,
        )
        existing_map: dict[str, dict[str, Any]] = {}
        for cid, raw in zip(card_ids, existing_list, strict=True):
            if isinstance(raw, Exception) or raw is None:
                continue
            existing_map[cid] = raw

        # Pass 1: resolve LWW + bury merging serially (pure CPU, no I/O).
        # Collects the put_item tasks so we can fire them in parallel below.
        result: dict[str, dict[str, Any]] = {}
        puts: list[tuple[str, dict[str, Any]]] = []
        for card_id, incoming in cards.items():
            existing_raw = existing_map.get(card_id)
            existing_payload = (
                _item_to_state(existing_raw) if existing_raw else None
            )
            existing_last = (
                str(existing_raw.get("lastReviewedAt") or "") if existing_raw else ""
            )
            incoming_last = str(incoming.get("lastReviewedAt") or "")

            # Why: ISO-8601 sorts lexicographically — safe string compare.
            server_wins = (
                existing_payload is not None and existing_last >= incoming_last
            )

            bury_changed = (
                existing_payload is not None
                and "buriedUntil" in incoming
                and incoming.get("buriedUntil") != existing_payload.get("buriedUntil")
            )

            if server_wins and not bury_changed:
                result[card_id] = existing_payload  # type: ignore[assignment]
                continue

            if server_wins and bury_changed and existing_payload is not None:
                incoming = {
                    **existing_payload,
                    "buriedUntil": incoming.get("buriedUntil"),
                }

            puts.append((card_id, incoming))
            result[card_id] = incoming

        # Pass 2: fire all put_item calls in parallel (Low debt fix —
        # matches the parallelization already used for the get_item pass
        # above). For typical sync batches (≤100 cards) we stay well below
        # Dynamo's per-table write-rate ceiling.
        if puts:
            await asyncio.gather(
                *[
                    self._table.put_item(Item=_state_to_item(user_id, cid, payload))
                    for cid, payload in puts
                ]
            )

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
