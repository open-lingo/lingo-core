"""DynamoDB-backed quest repository.

Single-table design (matches what Terraform provisions for ``lingo_quests``):
  PK = ``USER#<user_id>``    SK = ``QUEST#<quest_id>``

Attributes are stored top-level (not nested) so UpdateItem can target
individual fields. All fields mirror the SqliteQuestRepository row
shape one-to-one — same keys, same types — so the FastAPI router is
backend-agnostic.

State machine: ``active`` -> ``claimable`` (progress.current >= target)
-> ``completed`` (after explicit claim). Lazy ``expired`` is computed
by the router on read; we don't try to flip it server-side.

Numeric note: DynamoDB returns all numbers as Decimal. We coerce
ints back via ``_decimal_to_int`` when materialising rows.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from botocore.exceptions import ClientError

from app.db.dynamo._session import get_shared_resource

_QUEST_SK_PREFIX = "QUEST#"


def _pk(user_id: str) -> str:
    return f"USER#{user_id}"


def _sk(quest_id: str) -> str:
    return f"{_QUEST_SK_PREFIX}{quest_id}"


def _decimal_to_int(val: Any) -> int:
    if val is None:
        return 0
    return int(val) if isinstance(val, Decimal) else int(val)


def _item_to_quest(item: dict[str, Any]) -> dict[str, Any]:
    """Strip table keys + coerce Decimals back to ints. Mirrors the SQLite
    ``_row_to_quest`` shape exactly so callers don't care which backend."""
    return {
        "id": item["id"],
        "user_id": item["user_id"],
        "type": item["type"],
        "title_key": item.get("title_key") or "",
        "description_key": item.get("description_key") or "",
        "emoji": item.get("emoji") or "",
        "progress_current": _decimal_to_int(item.get("progress_current", 0)),
        "progress_target": _decimal_to_int(item.get("progress_target", 0)),
        "progress_unit": item.get("progress_unit") or "",
        "reward_lingots": _decimal_to_int(item.get("reward_lingots", 0)),
        "reward_xp": _decimal_to_int(item.get("reward_xp", 0)),
        "reward_ad_free_minutes": _decimal_to_int(item.get("reward_ad_free_minutes", 0)),
        "reward_streak_shield": bool(item.get("reward_streak_shield")),
        "status": item.get("status") or "active",
        "friend_id": item.get("friend_id"),
        "friend_display_name": item.get("friend_display_name"),
        "expires_at": item.get("expires_at"),
        "reward_granted": bool(item.get("reward_granted")),
        "created_at": item.get("created_at") or "",
    }


async def _paginate_query(table: Any, **kwargs: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    resp = await table.query(**kwargs)
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = await table.query(**kwargs, ExclusiveStartKey=resp["LastEvaluatedKey"])
        items.extend(resp.get("Items", []))
    return items


class DynamoQuestRepository:
    """DynamoDB-backed QuestRepository.

    Required table:
      PK (S) + SK (S), PAY_PER_REQUEST billing.

    No GSIs needed — every operation is scoped to a single user (PK).
    """

    def __init__(self, table_name: str, region: str) -> None:
        self._table_name = table_name
        self._region = region
        self._table: Any = None

    async def connect(self) -> None:
        resource = await get_shared_resource(self._region)
        self._table = await resource.Table(self._table_name)

    async def close(self) -> None:
        # Shared aioboto3 resource is closed once via close_shared_resource();
        # this repo's close is a no-op.
        pass

    # -- Reads --

    async def list_quests(self, user_id: str) -> list[dict[str, Any]]:
        items = await _paginate_query(
            self._table,
            KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
            ExpressionAttributeValues={
                ":pk": _pk(user_id),
                ":prefix": _QUEST_SK_PREFIX,
            },
        )
        rows = [_item_to_quest(it) for it in items]
        # Match SQLite's "ORDER BY created_at DESC".
        rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
        return rows

    async def get_quest(self, user_id: str, quest_id: str) -> dict[str, Any] | None:
        resp = await self._table.get_item(Key={"PK": _pk(user_id), "SK": _sk(quest_id)})
        item = resp.get("Item")
        return _item_to_quest(item) if item else None

    # -- Writes --

    async def put_quest(self, quest: dict[str, Any]) -> dict[str, Any]:
        """Upsert a quest row. Returns the persisted row."""
        created_at = quest.get("created_at") or datetime.now(UTC).isoformat()
        user_id = quest["user_id"]
        quest_id = quest["id"]

        item: dict[str, Any] = {
            "PK": _pk(user_id),
            "SK": _sk(quest_id),
            "id": quest_id,
            "user_id": user_id,
            "type": quest["type"],
            "title_key": quest.get("title_key") or "",
            "description_key": quest.get("description_key") or "",
            "emoji": quest.get("emoji") or "",
            "progress_current": int(quest.get("progress_current") or 0),
            "progress_target": int(quest["progress_target"]),
            "progress_unit": quest.get("progress_unit") or "",
            "reward_lingots": int(quest.get("reward_lingots") or 0),
            "reward_xp": int(quest.get("reward_xp") or 0),
            "reward_ad_free_minutes": int(quest.get("reward_ad_free_minutes") or 0),
            "reward_streak_shield": bool(quest.get("reward_streak_shield")),
            "status": quest.get("status") or "active",
            "friend_id": quest.get("friend_id"),
            "friend_display_name": quest.get("friend_display_name"),
            "expires_at": quest.get("expires_at"),
            "reward_granted": bool(quest.get("reward_granted")),
            "created_at": created_at,
        }
        await self._table.put_item(Item=item)
        return _item_to_quest(item)

    async def update_progress(
        self, user_id: str, quest_id: str, delta: int
    ) -> dict[str, Any] | None:
        """Bump ``progress_current`` by ``delta`` (capped at target). Flips
        status to ``claimable`` if the new value crosses the target.

        Matches SQLite semantics exactly:
        - returns None if the quest doesn't exist
        - returns the unchanged row if status is ``completed`` or ``expired``
        - clamps to ``[0, target]``
        - only flips ``active -> claimable``; never reverses
        """
        current = await self.get_quest(user_id, quest_id)
        if current is None:
            return None
        if current["status"] in ("completed", "expired"):
            return current

        target = int(current["progress_target"])
        new_current = max(0, min(target, current["progress_current"] + int(delta)))
        new_status = current["status"]
        if new_current >= target and new_status == "active":
            new_status = "claimable"

        resp = await self._table.update_item(
            Key={"PK": _pk(user_id), "SK": _sk(quest_id)},
            UpdateExpression="SET progress_current = :pc, #s = :st",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":pc": new_current, ":st": new_status},
            ReturnValues="ALL_NEW",
        )
        attrs = resp.get("Attributes")
        return _item_to_quest(attrs) if attrs else None

    async def claim(self, user_id: str, quest_id: str) -> dict[str, Any] | None:
        """Mark the quest ``completed`` and stamp ``reward_granted`` true.

        Atomic via ConditionExpression ``status = claimable``. On condition
        failure we read back the row to disambiguate:
        - status == completed → return current row (idempotent no-op)
        - anything else → return None (caller surfaces 409)
        Returns None if the row doesn't exist.
        """
        try:
            resp = await self._table.update_item(
                Key={"PK": _pk(user_id), "SK": _sk(quest_id)},
                UpdateExpression="SET #s = :completed, reward_granted = :true",
                ConditionExpression="attribute_exists(PK) AND #s = :claimable",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":completed": "completed",
                    ":claimable": "claimable",
                    ":true": True,
                },
                ReturnValues="ALL_NEW",
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
            # Condition failed — disambiguate via fresh read.
            current = await self.get_quest(user_id, quest_id)
            if current is None:
                return None
            if current["status"] == "completed":
                return current
            return None

        attrs = resp.get("Attributes")
        return _item_to_quest(attrs) if attrs else None

    async def delete_user_quests(
        self, user_id: str, types: list[str] | None = None
    ) -> int:
        """Delete all quests for a user (optionally filtered by type)."""
        items = await _paginate_query(
            self._table,
            KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
            ExpressionAttributeValues={
                ":pk": _pk(user_id),
                ":prefix": _QUEST_SK_PREFIX,
            },
            ProjectionExpression="PK, SK, #t",
            ExpressionAttributeNames={"#t": "type"},
        )

        if types is not None:
            type_set = set(types)
            items = [it for it in items if it.get("type") in type_set]

        if not items:
            return 0

        async with self._table.batch_writer() as batch:
            for it in items:
                await batch.delete_item(Key={"PK": it["PK"], "SK": it["SK"]})

        return len(items)
