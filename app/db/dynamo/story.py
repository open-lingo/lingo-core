"""DynamoDB-backed story repository.

Single-table design — one item per story:
  PK = STORY#<story_id>   SK = "META"   → the story row

Listing access patterns:
  - By ``language_id`` (optionally + ``status``): Query ``LanguageStatusIndex``
    (hash=language_id, range=status_updated_at = "<status>#<updated_at>").
    The status filter becomes a ``begins_with(status_updated_at, "<status>#")``
    range condition — single Query, no scan.
  - By ``author_id``: Query ``AuthorIndex`` (hash=author_id, range=created_at).
  - Otherwise: Scan with FilterExpression on ``SK = "META"``. Stories table is
    bounded today (no records in prod yet) — Scan is the cheapest impl until it
    grows.

Storage attributes — snake_case for GSI keys (matches Terraform), with the
camelCase router contract surfaced on read via ``_item_to_story``. The SQLite
reference impl in ``app/db/sqlite/story.py`` returns camelCase, so we do too.
"""

from datetime import UTC, datetime
from typing import Any

from app.db.dynamo._session import get_shared_resource

_META_SK = "META"
LANGUAGE_STATUS_INDEX = "LanguageStatusIndex"
AUTHOR_INDEX = "AuthorIndex"


def _pk(story_id: str) -> str:
    return f"STORY#{story_id}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _status_updated_at(status: str, updated_at: str) -> str:
    return f"{status}#{updated_at}"


def _item_to_story(item: dict[str, Any]) -> dict[str, Any]:
    """Translate the stored Dynamo item to the router's camelCase contract."""
    return {
        "id": item["id"],
        "languageId": item.get("language_id", ""),
        "title": item.get("title", ""),
        "description": item.get("description"),
        "companionDeckId": item.get("companion_deck_id", ""),
        "body": item.get("body", ""),
        "authorId": item.get("author_id"),
        "status": item.get("status", "draft"),
        "createdAt": item.get("created_at"),
        "updatedAt": item.get("updated_at"),
    }


async def _paginate_scan(table: Any, **kwargs: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    resp = await table.scan(**kwargs)
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = await table.scan(**kwargs, ExclusiveStartKey=resp["LastEvaluatedKey"])
        items.extend(resp.get("Items", []))
    return items


async def _paginate_query(table: Any, **kwargs: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    resp = await table.query(**kwargs)
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = await table.query(**kwargs, ExclusiveStartKey=resp["LastEvaluatedKey"])
        items.extend(resp.get("Items", []))
    return items


class DynamoStoryRepository:
    """DynamoDB-backed story repository.

    Required table layout (see ``lingo-infra/main.tf`` ``lingo_stories``):
      PK (S) + SK (S).
      GSI ``LanguageStatusIndex``  hash=language_id (S)  range=status_updated_at (S)
      GSI ``AuthorIndex``          hash=author_id (S)    range=created_at (S)
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

    # ── Reads ─────────────────────────────────────────────────────────────

    async def list_stories(
        self,
        author_id: str | None = None,
        language_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        # Language(+status) — single Query on LanguageStatusIndex.
        if language_id:
            values: dict[str, Any] = {":lang": language_id}
            key_cond = "language_id = :lang"
            if status:
                key_cond += " AND begins_with(status_updated_at, :sp)"
                values[":sp"] = f"{status}#"
            q_kwargs: dict[str, Any] = {
                "IndexName": LANGUAGE_STATUS_INDEX,
                "KeyConditionExpression": key_cond,
                "ExpressionAttributeValues": values,
                "ScanIndexForward": False,
            }
            # The GSI partitions by language_id, so author_id becomes a filter.
            if author_id:
                q_kwargs["FilterExpression"] = "author_id = :author"
                values[":author"] = author_id
            items = await _paginate_query(self._table, **q_kwargs)

        # Author only — Query AuthorIndex.
        elif author_id:
            q_kwargs = {
                "IndexName": AUTHOR_INDEX,
                "KeyConditionExpression": "author_id = :author",
                "ExpressionAttributeValues": {":author": author_id},
                "ScanIndexForward": False,
            }
            if status:
                q_kwargs["FilterExpression"] = "#st = :status"
                q_kwargs["ExpressionAttributeNames"] = {"#st": "status"}
                q_kwargs["ExpressionAttributeValues"][":status"] = status
            items = await _paginate_query(self._table, **q_kwargs)

        # No filters (or status only) — Scan. Stories volume is bounded.
        else:
            kwargs: dict[str, Any] = {
                "FilterExpression": "SK = :sk",
                "ExpressionAttributeValues": {":sk": _META_SK},
            }
            if status:
                kwargs["FilterExpression"] += " AND #st = :status"
                kwargs["ExpressionAttributeNames"] = {"#st": "status"}
                kwargs["ExpressionAttributeValues"][":status"] = status
            items = await _paginate_scan(self._table, **kwargs)

        stories = [_item_to_story(item) for item in items]
        # Newest updated first, falling back to title for ties — mirrors SQLite.
        stories.sort(key=lambda s: (s.get("updatedAt") or "", s.get("title") or ""))
        stories.sort(key=lambda s: s.get("updatedAt") or "", reverse=True)
        return stories

    async def get_story(self, story_id: str) -> dict[str, Any] | None:
        resp = await self._table.get_item(Key={"PK": _pk(story_id), "SK": _META_SK})
        item = resp.get("Item")
        return _item_to_story(item) if item else None

    # ── Writes ────────────────────────────────────────────────────────────

    async def create_story(self, story_id: str, data: dict[str, Any]) -> None:
        now = _now_iso()
        status = data.get("status") or "draft"
        item: dict[str, Any] = {
            "PK": _pk(story_id),
            "SK": _META_SK,
            "id": story_id,
            "language_id": data.get("languageId") or "",
            "title": data.get("title") or "",
            "description": data.get("description"),
            "companion_deck_id": data.get("companionDeckId") or "",
            "body": data.get("body") or "",
            "author_id": data.get("authorId"),
            "status": status,
            "created_at": now,
            "updated_at": now,
            "status_updated_at": _status_updated_at(status, now),
        }
        # Strip explicit None — Dynamo rejects None values, and we want the
        # author_id/description attrs simply absent so GSI projections behave.
        item = {k: v for k, v in item.items() if v is not None}
        await self._table.put_item(Item=item)

    async def update_story(self, story_id: str, data: dict[str, Any]) -> None:
        existing_raw = await self._table.get_item(
            Key={"PK": _pk(story_id), "SK": _META_SK}
        )
        existing = existing_raw.get("Item")
        if not existing:
            return

        merged = dict(existing)
        # Translate camelCase patch keys to snake_case stored attributes.
        key_map = {
            "languageId": "language_id",
            "title": "title",
            "description": "description",
            "companionDeckId": "companion_deck_id",
            "body": "body",
            "authorId": "author_id",
            "status": "status",
        }
        for k, v in data.items():
            target = key_map.get(k)
            if target is None:
                continue
            # Mirror SQLite behaviour: description/body accept explicit None
            # (lets a clearer clear them); other fields skip None.
            if v is None and target not in ("description", "body"):
                continue
            merged[target] = v

        now = _now_iso()
        merged["updated_at"] = now
        merged["status_updated_at"] = _status_updated_at(
            merged.get("status") or "draft", now
        )

        # Strip None — Dynamo rejects null attribute values.
        merged = {k: v for k, v in merged.items() if v is not None}
        await self._table.put_item(Item=merged)

    async def delete_story(self, story_id: str) -> None:
        await self._table.delete_item(Key={"PK": _pk(story_id), "SK": _META_SK})
