"""DynamoDB-backed progress repository (production).

Single-table layout per ADR-0001:

  PK = USER#<uuid>
  SK = ATTEMPT#<lessonId>#<isoTs>  — attempt log (writes user_id + attemptedAt for GSI)
  SK = CLIENT#<clientAttemptId>     — idempotency lookup
  SK = LESSON#<lessonId> | DAY#<date> | CONCEPT#<conceptId>

GSI ``UserAttempts-Index``: hash ``user_id``, range ``attemptedAt``.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any, Final

from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

from app.db.dynamo._session import get_shared_resource

logger: Final = logging.getLogger("lingo.dynamo")

_SERIALIZER = TypeSerializer()


def _to_attr_map(item: dict[str, Any]) -> dict[str, Any]:
    """Convert a Python dict to the DynamoDB AttributeValue map shape
    required by the low-level transact_write_items API."""
    return {k: _SERIALIZER.serialize(v) for k, v in item.items()}


_USER_PREFIX = "USER#"
_ATTEMPT_PREFIX = "ATTEMPT#"
_CLIENT_PREFIX = "CLIENT#"
_LESSON_PREFIX = "LESSON#"
_DAY_PREFIX = "DAY#"
_CONCEPT_PREFIX = "CONCEPT#"
_GSI_ATTEMPTS = "UserAttempts-Index"


def _pk(user_id: str) -> str:
    return f"{_USER_PREFIX}{user_id}"


def _to_decimal(val: float | int) -> Decimal:
    return Decimal(str(val))


def _decimal_to_float(val: Any) -> float:
    return float(val) if isinstance(val, Decimal) else float(val)


def _decimal_to_int(val: Any) -> int:
    return int(val) if isinstance(val, Decimal) else int(val)


def _attempt_item_to_dict(item: dict[str, Any]) -> dict[str, Any]:
    steps = item.get("steps")
    if isinstance(steps, str):
        steps = json.loads(steps)
    return {
        "attemptId": item["attemptId"],
        "clientAttemptId": item["clientAttemptId"],
        "lessonId": item["lessonId"],
        "attemptedAt": item["attemptedAt"],
        "durationSec": _decimal_to_int(item["durationSec"]),
        "passed": bool(item.get("passed")),
        "score": _decimal_to_float(item["score"]),
        "steps": steps or [],
    }


def _lesson_item_to_dict(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "lessonId": item["lessonId"],
        "bestScore": _decimal_to_float(item.get("bestScore", 0)),
        "firstPassedAt": item.get("firstPassedAt"),
        "latestAttemptAt": item["latestAttemptAt"],
        "attemptCount": _decimal_to_int(item.get("attemptCount", 0)),
    }


def _day_item_to_dict(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": item["date"],
        "lessonsCompleted": _decimal_to_int(item.get("lessonsCompleted", 0)),
        "minutesActive": _decimal_to_int(item.get("minutesActive", 0)),
        "xpEarned": _decimal_to_int(item.get("xpEarned", 0)),
    }


def _concept_item_to_dict(item: dict[str, Any]) -> dict[str, Any]:
    recent = item.get("recentResults")
    if isinstance(recent, str):
        recent = json.loads(recent)
    avg = item.get("avgDurationMs")
    return {
        "conceptId": item["conceptId"],
        "encounters": _decimal_to_int(item.get("encounters", 0)),
        "correctCount": _decimal_to_int(item.get("correctCount", 0)),
        "incorrectCount": _decimal_to_int(item.get("incorrectCount", 0)),
        "recentResults": recent or [],
        "avgDurationMs": _decimal_to_int(avg) if avg is not None else None,
        "firstSeenAt": item.get("firstSeenAt", ""),
        "lastSeenAt": item.get("lastSeenAt", ""),
        "lastCorrectAt": item.get("lastCorrectAt"),
        "staleAt": item.get("staleAt"),
    }


async def _paginate_query(table: Any, **kwargs: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    resp = await table.query(**kwargs)
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = await table.query(**kwargs, ExclusiveStartKey=resp["LastEvaluatedKey"])
        items.extend(resp.get("Items", []))
    return items


class DynamoProgressRepository:
    """Progress table + ``UserAttempts-Index`` (see ``lingo-infra/main.tf``)."""

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

    async def attempt_exists(self, user_id: str, client_attempt_id: str) -> dict[str, Any] | None:
        resp = await self._table.get_item(Key={"PK": _pk(user_id), "SK": f"{_CLIENT_PREFIX}{client_attempt_id}"})
        item = resp.get("Item")
        return _attempt_item_to_dict(item) if item else None

    async def update_attempt_steps(
        self,
        user_id: str,
        client_attempt_id: str,
        steps: list[dict[str, Any]],
    ) -> None:
        # Draft mid-lesson recovery: persist the step list on the CLIENT# item
        # so a half-finished lesson is recoverable across devices. No XP is
        # awarded here (that lands once on the final lesson_completed attempt).
        # ConditionExpression guards against creating an item for an attempt
        # that put_attempt hasn't written yet — caller only invokes this when
        # attempt_exists() already returned the row.
        try:
            await self._table.update_item(
                Key={"PK": _pk(user_id), "SK": f"{_CLIENT_PREFIX}{client_attempt_id}"},
                UpdateExpression="SET steps = :steps",
                ConditionExpression="attribute_exists(PK)",
                ExpressionAttributeValues={":steps": steps},
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                # Attempt row vanished between the existence check and here —
                # nothing to update; the next full sync will recreate it.
                return
            raise

    async def put_attempt(self, user_id: str, attempt: dict[str, Any]) -> None:
        if await self.attempt_exists(user_id, attempt["clientAttemptId"]):
            return

        lesson_id = attempt["lessonId"]
        attempted_at = attempt["attemptedAt"]
        base: dict[str, Any] = {
            "PK": _pk(user_id),
            "attemptId": attempt["attemptId"],
            "clientAttemptId": attempt["clientAttemptId"],
            "lessonId": lesson_id,
            "attemptedAt": attempted_at,
            "user_id": user_id,
            "durationSec": int(attempt["durationSec"]),
            "passed": attempt["passed"],
            "score": _to_decimal(attempt["score"]),
            "steps": attempt.get("steps", []),
        }
        attempt_item = {
            **base,
            "SK": f"{_ATTEMPT_PREFIX}{lesson_id}#{attempted_at}",
        }
        client_item = {
            **base,
            "SK": f"{_CLIENT_PREFIX}{attempt['clientAttemptId']}",
        }

        # Fix 3 — atomic two-item write. Previously the second PutItem could
        # fail after the first succeeded, leaving an orphan ATTEMPT row that
        # made subsequent idempotency lookups miss and the retry's
        # ConditionExpression fail. TransactWriteItems is 2x WCU but
        # eliminates that failure mode.
        #
        # NOTE — `Item=` passes the native Python dict directly. The
        # resource-attached client (`self._table.meta.client`) registers a
        # `before-parameter-build.dynamodb.TransactWriteItems` handler that
        # serializes each value via TypeSerializer. Pre-serializing here
        # would DOUBLE-serialize and produce a `Type mismatch ... expected: S
        # actual: M` ValidationError on every write — the bug that left
        # lingo_progress at 0 items for ~12 days.
        client = self._table.meta.client
        try:
            await client.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self._table_name,
                            "Item": attempt_item,
                            "ConditionExpression": "attribute_not_exists(SK)",
                        }
                    },
                    {
                        "Put": {
                            "TableName": self._table_name,
                            "Item": client_item,
                            "ConditionExpression": "attribute_not_exists(SK)",
                        }
                    },
                ]
            )
        except ClientError as exc:
            # DIAGNOSTIC (temporary): the boto summary only prints
            # "[ValidationError, None]" and hides which field is malformed.
            # The real per-item reason (with a human Message) lives in the
            # response's CancellationReasons. Log it plus the item-shape
            # fingerprint so we can pin the bad draft attempt, then re-raise
            # unchanged. Remove once root cause is fixed.
            reasons = exc.response.get("CancellationReasons")
            logger.warning(
                "put_attempt transact failed: code=%s reasons=%s "
                "fingerprint={attempt_sk=%r client_sk=%r attemptedAt=%r "
                "lessonId=%r score=%r durationSec=%r isDraft=%s steps=%d}",
                exc.response.get("Error", {}).get("Code"),
                reasons,
                attempt_item["SK"],
                client_item["SK"],
                attempted_at,
                lesson_id,
                base["score"],
                base["durationSec"],
                attempt["clientAttemptId"].startswith("draft:"),
                len(base["steps"]),
            )
            raise

    async def list_attempts(
        self,
        user_id: str,
        lesson_id: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        fetch = limit + 1
        if lesson_id:
            kwargs: dict[str, Any] = {
                "KeyConditionExpression": Key("PK").eq(_pk(user_id)) & Key("SK").begins_with(f"{_ATTEMPT_PREFIX}{lesson_id}#"),
                "ScanIndexForward": False,
                "Limit": fetch,
            }
            if cursor:
                kwargs["ExclusiveStartKey"] = {
                    "PK": _pk(user_id),
                    "SK": f"{_ATTEMPT_PREFIX}{lesson_id}#{cursor}",
                }
            rows = await _paginate_query(self._table, **kwargs)
        else:
            key_expr = Key("user_id").eq(user_id)
            if cursor:
                key_expr = key_expr & Key("attemptedAt").lt(cursor)
            kwargs = {
                "IndexName": _GSI_ATTEMPTS,
                "KeyConditionExpression": key_expr,
                "ScanIndexForward": False,
                "Limit": fetch,
            }
            rows = await _paginate_query(self._table, **kwargs)

        items = [_attempt_item_to_dict(r) for r in rows[:limit]]
        next_cursor = items[-1]["attemptedAt"] if len(rows) > limit else None
        return items, next_cursor

    async def get_attempts_for_concepts(
        self,
        user_id: str,
        concept_ids: list[str],
        since: str | None = None,
    ) -> list[dict[str, Any]]:
        if not concept_ids:
            return []
        concept_set = set(concept_ids)
        expr = Key("user_id").eq(user_id)
        if since:
            expr = expr & Key("attemptedAt").gte(since)
        rows = await _paginate_query(
            self._table,
            IndexName=_GSI_ATTEMPTS,
            KeyConditionExpression=expr,
            ScanIndexForward=False,
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            attempt = _attempt_item_to_dict(row)
            if any(cid in concept_set for step in attempt["steps"] for cid in (step.get("conceptIds") or [])):
                out.append(attempt)
        return out

    async def update_lesson_rollup(self, user_id: str, lesson_id: str, attempt: dict[str, Any]) -> dict[str, Any]:
        sk = f"{_LESSON_PREFIX}{lesson_id}"
        key = {"PK": _pk(user_id), "SK": sk}
        score = float(attempt["score"])
        attempted_at = attempt["attemptedAt"]
        passed = bool(attempt["passed"])

        resp = await self._table.get_item(Key=key)
        existing = resp.get("Item")
        if not existing:
            item: dict[str, Any] = {
                **key,
                "lessonId": lesson_id,
                "bestScore": _to_decimal(score),
                "latestAttemptAt": attempted_at,
                "attemptCount": 1,
            }
            if passed:
                item["firstPassedAt"] = attempted_at
            await self._table.put_item(Item=item)
            return _lesson_item_to_dict(item)

        best = max(_decimal_to_float(existing.get("bestScore", 0)), score)
        first_passed = existing.get("firstPassedAt")
        if passed and not first_passed:
            first_passed = attempted_at
        item = {
            **key,
            "lessonId": lesson_id,
            "bestScore": _to_decimal(best),
            "latestAttemptAt": attempted_at,
            "attemptCount": _decimal_to_int(existing.get("attemptCount", 0)) + 1,
        }
        if first_passed:
            item["firstPassedAt"] = first_passed
        await self._table.put_item(Item=item)
        return _lesson_item_to_dict(item)

    async def get_lesson_rollups(self, user_id: str) -> list[dict[str, Any]]:
        rows = await _paginate_query(
            self._table,
            KeyConditionExpression=Key("PK").eq(_pk(user_id)) & Key("SK").begins_with(_LESSON_PREFIX),
        )
        return [_lesson_item_to_dict(r) for r in rows]

    async def update_day_rollup(
        self,
        user_id: str,
        date: str,
        lessons_inc: int,
        minutes_inc: int,
        xp_inc: int,
    ) -> dict[str, Any]:
        sk = f"{_DAY_PREFIX}{date}"
        key = {"PK": _pk(user_id), "SK": sk}
        await self._table.update_item(
            Key=key,
            UpdateExpression=("ADD lessonsCompleted :lc, minutesActive :ma, xpEarned :xp SET #d = if_not_exists(#d, :date)"),
            ExpressionAttributeNames={"#d": "date"},
            ExpressionAttributeValues={
                ":lc": lessons_inc,
                ":ma": minutes_inc,
                ":xp": xp_inc,
                ":date": date,
            },
        )
        resp = await self._table.get_item(Key=key)
        return _day_item_to_dict(resp["Item"])

    async def get_day_rollups(self, user_id: str, since: str, until: str) -> list[dict[str, Any]]:
        rows = await _paginate_query(
            self._table,
            KeyConditionExpression=Key("PK").eq(_pk(user_id)) & Key("SK").between(f"{_DAY_PREFIX}{since}", f"{_DAY_PREFIX}{until}"),
        )
        return [_day_item_to_dict(r) for r in rows]

    async def invalidate_concepts(self, user_id: str, concept_ids: list[str], staleAt: str) -> None:
        for cid in concept_ids:
            await self._table.update_item(
                Key={"PK": _pk(user_id), "SK": f"{_CONCEPT_PREFIX}{cid}"},
                UpdateExpression=(
                    "SET staleAt = :s, lastSeenAt = :s, conceptId = :cid, "
                    "encounters = if_not_exists(encounters, :z), "
                    "correctCount = if_not_exists(correctCount, :z), "
                    "incorrectCount = if_not_exists(incorrectCount, :z), "
                    "recentResults = if_not_exists(recentResults, :empty), "
                    "firstSeenAt = if_not_exists(firstSeenAt, :s)"
                ),
                ExpressionAttributeValues={
                    ":s": staleAt,
                    ":cid": cid,
                    ":z": 0,
                    ":empty": [],
                },
            )

    async def get_concept_rollups(self, user_id: str) -> list[dict[str, Any]]:
        rows = await _paginate_query(
            self._table,
            KeyConditionExpression=Key("PK").eq(_pk(user_id)) & Key("SK").begins_with(_CONCEPT_PREFIX),
        )
        return [_concept_item_to_dict(r) for r in rows]

    async def put_concept_rollup(self, user_id: str, rollup: dict[str, Any]) -> None:
        cid = rollup["conceptId"]
        item: dict[str, Any] = {
            "PK": _pk(user_id),
            "SK": f"{_CONCEPT_PREFIX}{cid}",
            "conceptId": cid,
            "encounters": int(rollup.get("encounters", 0)),
            "correctCount": int(rollup.get("correctCount", 0)),
            "incorrectCount": int(rollup.get("incorrectCount", 0)),
            "recentResults": rollup.get("recentResults", []),
            "firstSeenAt": rollup["firstSeenAt"],
            "lastSeenAt": rollup["lastSeenAt"],
        }
        if rollup.get("avgDurationMs") is not None:
            item["avgDurationMs"] = int(rollup["avgDurationMs"])
        if rollup.get("lastCorrectAt"):
            item["lastCorrectAt"] = rollup["lastCorrectAt"]
        await self._table.put_item(Item=item)

    async def delete_all_for_user(self, user_id: str) -> None:
        items = await _paginate_query(
            self._table,
            KeyConditionExpression=Key("PK").eq(_pk(user_id)),
            ProjectionExpression="PK, SK",
        )
        if not items:
            return
        async with self._table.batch_writer() as batch:
            for item in items:
                await batch.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})
