"""DynamoDB-backed deck repository.

Single-table design:
  PK = DECK#<deck_id>   SK = META   → one item per deck (manifest + cards)

Cards are stored as a JSON string to avoid DynamoDB's 400 KB item limit
surprises with deeply nested lists, and to simplify type handling.

Listing:
  - By ``author_id``: Query ``AuthorUpdated-Index`` (GSI on ``authorId`` +
    ``authorUpdatedDeck`` = ``<ISO updatedAt>#<deck_id>``). Avoids full-table
    scans for “My decks” / edit flows.
  - By ``status`` (+ optional ``language_id``) without author: Query
    ``StatusLanguage-Index``.
  - Otherwise: Scan + FilterExpression (admin-style listings).
"""

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.db.dynamo._session import get_shared_resource

_META_SK = "META"
AUTHOR_UPDATED_INDEX = "AuthorUpdated-Index"


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
      PK (S) + SK (S). GSIs: ``StatusLanguage-Index``, ``AuthorUpdated-Index``.

    Create / update this table — see ``lingo-infra/main.tf`` or README AWS CLI
    snippet for attribute definitions and index specs.
    """

    def __init__(self, table_name: str, region: str, votes_table_name: str | None = None) -> None:
        self._table_name = table_name
        self._votes_table_name = votes_table_name
        self._region = region
        self._table: Any = None
        self._votes_table: Any = None

    async def connect(self) -> None:
        resource = await get_shared_resource(self._region)
        self._table = await resource.Table(self._table_name)
        if self._votes_table_name:
            self._votes_table = await resource.Table(self._votes_table_name)

    async def close(self) -> None:
        # Shared resource closed via close_shared_resource(); no-op here.
        pass

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
        # Author-scoped listings (My decks): Query AuthorUpdated-Index — O(decks by author).
        if author_id:
            values: dict[str, Any] = {":author": author_id}
            filters: list[str] = []
            names: dict[str, str] = {}
            if language_id:
                filters.append("languageId = :lang")
                values[":lang"] = language_id
            if status:
                filters.append("#st = :status")
                values[":status"] = status
                names["#st"] = "status"
            if exclude_companion:
                filters.append("attribute_not_exists(companionToStoryId)")
            q_kwargs: dict[str, Any] = {
                "IndexName": AUTHOR_UPDATED_INDEX,
                "KeyConditionExpression": "authorId = :author",
                "ExpressionAttributeValues": values,
                "ScanIndexForward": False,
            }
            if filters:
                q_kwargs["FilterExpression"] = " AND ".join(filters)
            if names:
                q_kwargs["ExpressionAttributeNames"] = names
            items = await _paginate_query(self._table, **q_kwargs)
        elif status and not author_id:
            key_cond = "#st = :status"
            values = {":status": status}
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

    async def list_owned_manifests(
        self,
        author_id: str,
        *,
        language_id: str | None = None,
        status: str | None = None,
        exclude_companion: bool = False,
    ) -> list[dict[str, Any]]:
        """Decks authored by this user — uses ``AuthorUpdated-Index`` via ``list_manifests``."""
        return await self.list_manifests(
            language_id=language_id,
            author_id=author_id,
            status=status,
            exclude_companion=exclude_companion,
        )

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

    async def upsert_deck(self, deck_id: str, manifest: dict[str, Any], cards: list[dict[str, Any]]) -> None:
        now = datetime.now(UTC).isoformat()

        # Preserve existing authorId if not supplied in the new manifest
        existing = await self.get_manifest(deck_id)
        author_id = manifest.get("authorId") or (existing.get("authorId") if existing else None)
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

        # AuthorUpdated-Index: sort key groups decks per author, newest first on Query
        # with ScanIndexForward=False (ISO timestamps sort lexicographically).
        if author_id:
            item["authorUpdatedDeck"] = f"{now}#{deck_id}"

        # Remove None values — DynamoDB rejects explicit None attributes
        item = {k: v for k, v in item.items() if v is not None}

        await self._table.put_item(Item=item)

    async def delete_deck(self, deck_id: str) -> None:
        await self._table.delete_item(
            Key={"PK": self._pk(deck_id), "SK": _META_SK},
        )
        # Cascade votes — mirrors SQLite's foreign-key-style cleanup. Safe to
        # skip if the votes table isn't configured (test envs / older callers).
        if self._votes_table is not None:
            items = await _paginate_query(
                self._votes_table,
                KeyConditionExpression="PK = :pk",
                ExpressionAttributeValues={":pk": self._vote_pk(deck_id)},
                ProjectionExpression="PK, SK",
            )
            async with self._votes_table.batch_writer() as batch:
                for it in items:
                    await batch.delete_item(Key={"PK": it["PK"], "SK": it["SK"]})

    # ── Voting ────────────────────────────────────────────────────────────
    # Backed by a separate ``lingo_deck_votes`` table (see lingo-infra/main.tf).
    # Key layout: PK = DECK#<deck_id>, SK = USER#<user_id>. One row per vote.
    # add_vote is idempotent via attribute_not_exists(PK) — repeat votes
    # collapse into a no-op the same way SQLite's INSERT OR IGNORE does.
    # Vote counts come from a Query(Select=COUNT) per deck — fine at page-
    # render N≤20; materialize a counter on the deck manifest if it ever
    # becomes hot.

    def _votes(self) -> Any:
        if self._votes_table is None:
            raise RuntimeError("Deck votes table not configured (votes_table_name=None)")
        return self._votes_table

    def _vote_pk(self, deck_id: str) -> str:
        return f"DECK#{deck_id}"

    def _vote_sk(self, user_id: str) -> str:
        return f"USER#{user_id}"

    async def add_vote(self, deck_id: str, user_id: str) -> None:
        now = datetime.now(UTC).isoformat()
        try:
            await self._votes().put_item(
                Item={
                    "PK": self._vote_pk(deck_id),
                    "SK": self._vote_sk(user_id),
                    "deck_id": deck_id,
                    "user_id": user_id,
                    "created_at": now,
                },
                ConditionExpression="attribute_not_exists(PK)",
            )
        except Exception as exc:  # noqa: BLE001
            # Already voted — idempotent no-op. botocore wraps the conditional
            # check failure; match by class name string to avoid importing
            # botocore.exceptions just for this.
            if "ConditionalCheckFailed" in str(type(exc).__name__) or "ConditionalCheckFailed" in str(exc):
                return
            raise

    async def remove_vote(self, deck_id: str, user_id: str) -> None:
        await self._votes().delete_item(
            Key={"PK": self._vote_pk(deck_id), "SK": self._vote_sk(user_id)},
        )

    async def get_vote_count(self, deck_id: str) -> int:
        # Select=COUNT paginates by returning Count + ScannedCount per page;
        # walk LastEvaluatedKey to handle decks with >1MB worth of voter rows
        # (unlikely in practice but cheap to be correct).
        total = 0
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": "PK = :pk",
            "ExpressionAttributeValues": {":pk": self._vote_pk(deck_id)},
            "Select": "COUNT",
        }
        resp = await self._votes().query(**kwargs)
        total += int(resp.get("Count", 0))
        while "LastEvaluatedKey" in resp:
            resp = await self._votes().query(**kwargs, ExclusiveStartKey=resp["LastEvaluatedKey"])
            total += int(resp.get("Count", 0))
        return total

    async def get_vote_state(self, deck_id: str, user_id: str | None) -> dict[str, Any]:
        count = await self.get_vote_count(deck_id)
        if user_id is None:
            return {"count": count, "voted": False}
        resp = await self._votes().get_item(
            Key={"PK": self._vote_pk(deck_id), "SK": self._vote_sk(user_id)},
        )
        return {"count": count, "voted": resp.get("Item") is not None}

    async def get_vote_counts(self, deck_ids: list[str]) -> dict[str, int]:
        if not deck_ids:
            return {}
        counts = await asyncio.gather(
            *[self.get_vote_count(d) for d in deck_ids],
            return_exceptions=False,
        )
        return dict(zip(deck_ids, counts))
