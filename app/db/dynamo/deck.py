"""DynamoDB-backed deck repository.

Single-table design:
  PK = DECK#<deck_id>   SK = META   → one item per deck (manifest + cards)

Cards are stored as a JSON string to avoid DynamoDB's 400 KB item limit
surprises with deeply nested lists, and to simplify type handling.

Listing uses Scan + FilterExpression.  At deck catalog scale this is
acceptable; add a GSI on language_id or status if query volume grows.
"""

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import aioboto3

_META_SK = "META"


def _to_decimal(val: float | None) -> Decimal | None:
    return Decimal(str(val)) if val is not None else None


def _item_to_manifest(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "languageId": item.get("languageId", ""),
        "name": item.get("name", ""),
        "description": item.get("description"),
        "courseId": item.get("courseId"),
        "authorId": item.get("authorId"),
        "status": item.get("status", "published"),
        "version": item.get("version", "1.0"),
        "cardCount": int(item.get("cardCount", 0)),
        "image": item.get("image"),
        "defaultEase": float(item["defaultEase"]) if item.get("defaultEase") is not None else None,
        "locale": item.get("locale"),
        "companionToStoryId": item.get("companionToStoryId"),
        "createdAt": item.get("createdAt"),
        "updatedAt": item.get("updatedAt"),
    }


async def _paginate_scan(table: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """Scan with automatic pagination."""
    items: list[dict[str, Any]] = []
    resp = await table.scan(**kwargs)
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = await table.scan(**kwargs, ExclusiveStartKey=resp["LastEvaluatedKey"])
        items.extend(resp.get("Items", []))
    return items


async def _paginate_query(table: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """Query with automatic pagination."""
    items: list[dict[str, Any]] = []
    resp = await table.query(**kwargs)
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = await table.query(**kwargs, ExclusiveStartKey=resp["LastEvaluatedKey"])
        items.extend(resp.get("Items", []))
    return items


class DynamoDeckRepository:
    """DynamoDB-backed deck repository.

    Required table:
      Table  PK (S) + SK (S)  — no GSIs required for basic operation

    Create this table (AWS CLI example):

      aws dynamodb create-table \\
        --table-name lingo_decks \\
        --attribute-definitions \\
            AttributeName=PK,AttributeType=S \\
            AttributeName=SK,AttributeType=S \\
        --key-schema AttributeName=PK,KeyType=HASH AttributeName=SK,KeyType=RANGE \\
        --billing-mode PAY_PER_REQUEST
    """

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

    def _pk(self, deck_id: str) -> str:
        return f"DECK#{deck_id}"

    # ── Protocol methods ──────────────────────────────────────────────────────

    async def list_manifests(
        self,
        language_id: str | None = None,
        author_id: str | None = None,
        status: str | None = None,
        exclude_companion: bool = False,
    ) -> list[dict[str, Any]]:
        # When status is set and author_id is not, use StatusLanguage-Index (Query)
        # When author_id is set, must Scan — no GSI on authorId
        if status and not author_id:
            key_cond = "#st = :status"
            values: dict[str, Any] = {":status": status}
            names = {"#st": "status"}
            if language_id:
                key_cond += " AND languageId = :lang"
                values[":lang"] = language_id
            items = await _paginate_query(
                self._table,
                IndexName="StatusLanguage-Index",
                KeyConditionExpression=key_cond,
                ExpressionAttributeValues=values,
                ExpressionAttributeNames=names,
            )
        else:
            conditions: list[str] = ["SK = :sk"]
            values = {":sk": _META_SK}
            if language_id:
                conditions.append("languageId = :lang")
                values[":lang"] = language_id
            if author_id:
                conditions.append("authorId = :author")
                values[":author"] = author_id
            if status:
                conditions.append("#st = :status")
                values[":status"] = status
            kwargs: dict[str, Any] = {
                "FilterExpression": " AND ".join(conditions),
                "ExpressionAttributeValues": values,
            }
            if status:
                kwargs["ExpressionAttributeNames"] = {"#st": "status"}
            items = await _paginate_scan(self._table, **kwargs)
        manifests = [_item_to_manifest(item) for item in items]
        if exclude_companion:
            manifests = [m for m in manifests if not m.get("companionToStoryId")]
        manifests.sort(key=lambda m: (m.get("updatedAt") or "", m.get("name") or ""), reverse=False)
        manifests.sort(key=lambda m: m.get("updatedAt") or "", reverse=True)
        return manifests

    async def get_manifest(self, deck_id: str) -> dict[str, Any] | None:
        resp = await self._table.get_item(
            Key={"PK": self._pk(deck_id), "SK": _META_SK},
        )
        item = resp.get("Item")
        return _item_to_manifest(item) if item else None

    async def get_deck(self, deck_id: str) -> dict[str, Any] | None:
        resp = await self._table.get_item(
            Key={"PK": self._pk(deck_id), "SK": _META_SK},
        )
        item = resp.get("Item")
        if not item:
            return None
        manifest = _item_to_manifest(item)
        cards = json.loads(item.get("cards", "[]"))
        return {**manifest, "cards": cards}

    async def get_decks_batch(self, deck_ids: list[str]) -> list[dict[str, Any]]:
        if not deck_ids:
            return []
        decks = await asyncio.gather(
            *[self.get_deck(did) for did in deck_ids],
            return_exceptions=True,
        )
        result: list[dict[str, Any]] = []
        for d in decks:
            if isinstance(d, Exception):
                continue
            if d is not None:
                result.append(d)
        return result

    async def get_versions(self, deck_ids: list[str]) -> dict[str, str]:
        if not deck_ids:
            return {}
        result: dict[str, str] = {}
        for deck_id in deck_ids:
            resp = await self._table.get_item(
                Key={"PK": self._pk(deck_id), "SK": _META_SK},
                ProjectionExpression="id, version",
            )
            item = resp.get("Item")
            if item:
                result[item["id"]] = item.get("version", "1.0")
        return result

    async def upsert_deck(
        self, deck_id: str, manifest: dict[str, Any], cards: list[dict[str, Any]]
    ) -> None:
        now = datetime.now(UTC).isoformat()

        # Preserve existing authorId if not supplied in the new manifest
        existing = await self.get_manifest(deck_id)
        author_id = manifest.get("authorId") or (
            existing.get("authorId") if existing else None
        )
        created_at = existing.get("createdAt", now) if existing else now

        default_ease = _to_decimal(manifest.get("defaultEase"))

        item: dict[str, Any] = {
            "PK": self._pk(deck_id),
            "SK": _META_SK,
            "id": deck_id,
            "languageId": manifest.get("languageId", ""),
            "name": manifest.get("name", ""),
            "description": manifest.get("description"),
            "courseId": manifest.get("courseId"),
            "authorId": author_id,
            "status": manifest.get("status", "draft"),
            "version": manifest.get("version", "1.0"),
            "cardCount": len(cards),
            "image": manifest.get("image"),
            "locale": manifest.get("locale"),
            "cards": json.dumps(cards),
            "createdAt": created_at,
            "updatedAt": now,
        }
        if default_ease is not None:
            item["defaultEase"] = default_ease
        if manifest.get("companionToStoryId") is not None:
            item["companionToStoryId"] = manifest["companionToStoryId"]

        # Remove None values — DynamoDB rejects explicit None attributes
        item = {k: v for k, v in item.items() if v is not None}

        await self._table.put_item(Item=item)
