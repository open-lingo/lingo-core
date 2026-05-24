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
from decimal import Decimal
from typing import Any

import aioboto3
from boto3.dynamodb.conditions import Attr, Key

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
        resp = await table.query(
            **kwargs, ExclusiveStartKey=resp["LastEvaluatedKey"]
        )
        items.extend(resp.get("Items", []))
    return items


class DynamoProgressRepository:
    """Progress table + ``UserAttempts-Index`` (see ``lingo-infra/main.tf``)."""

    def __init__(self, table_name: str, region: str) -> None:
        self._table_name = table_name
        self._region = region
        self._session = aioboto3.Session()
        self._table: Any = None
        self._resource_ctx: Any = None

    async def connect(self) -> None:
        self._resource_ctx = self._session.resource(
            "dynamodb", region_name=self._region
        )
        resource = await self._resource_ctx.__aenter__()
        self._table = await resource.Table(self._table_name)

    async def close(self) -> None:
        if self._resource_ctx:
            await self._resource_ctx.__aexit__(None, None, None)

    async def attempt_exists(
        self, user_id: str, client_attempt_id: str
    ) -> dict[str, Any] | None:
        resp = await self._table.get_item(
            Key={"PK": _pk(user_id), "SK": f"{_CLIENT_PREFIX}{client_attempt_id}"}
        )
        item = resp.get("Item")
        return _attempt_item_to_dict(item) if item else None

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
        await self._table.put_item(
            Item={**base, "SK": f"{_ATTEMPT_PREFIX}{lesson_id}#{attempted_at}"},
            ConditionExpression=Attr("SK").not_exists(),
        )
        await self._table.put_item(
            Item={**base, "SK": f"{_CLIENT_PREFIX}{attempt['clientAttemptId']}"},
            ConditionExpression=Attr("SK").not_exists(),
        )

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
                "KeyConditionExpression": Key("PK").eq(_pk(user_id))
                & Key("SK").begins_with(f"{_ATTEMPT_PREFIX}{lesson_id}#"),
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
            if any(
                cid in concept_set
                for step in attempt["steps"]
                for cid in (step.get("conceptIds") or [])
            ):
                out.append(attempt)
        return out

    async def update_lesson_rollup(
        self, user_id: str, lesson_id: str, attempt: dict[str, Any]
    ) -> dict[str, Any]:
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
            KeyConditionExpression=Key("PK").eq(_pk(user_id))
            & Key("SK").begins_with(_LESSON_PREFIX),
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
            UpdateExpression=(
                "ADD lessonsCompleted :lc, minutesActive :ma, xpEarned :xp "
                "SET #d = if_not_exists(#d, :date)"
            ),
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

    async def get_day_rollups(
        self, user_id: str, since: str, until: str
    ) -> list[dict[str, Any]]:
        rows = await _paginate_query(
            self._table,
            KeyConditionExpression=Key("PK").eq(_pk(user_id))
            & Key("SK").between(f"{_DAY_PREFIX}{since}", f"{_DAY_PREFIX}{until}"),
        )
        return [_day_item_to_dict(r) for r in rows]

    async def invalidate_concepts(
        self, user_id: str, concept_ids: list[str], staleAt: str
    ) -> None:
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
            KeyConditionExpression=Key("PK").eq(_pk(user_id))
            & Key("SK").begins_with(_CONCEPT_PREFIX),
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
