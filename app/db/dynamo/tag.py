"""DynamoDB-backed tag repository.

Single-table layout (mirrors ``lingo-infra/main.tf`` ``lingo_tags``):

  PK = SLUG#<slug>      SK = META               # canonical tag row
  PK = DECK#<deck_id>   SK = TAG#<slug>         # deck → tag mirror row
  GSI1PK = TAG#<slug>   GSI1SK = DECK#<deck_id> # reverse lookup (TagDeck-Index)

Reads:
  - list_tags()             — Scan + FilterExpression on SK = META. The
    canonical tag dictionary is admin-curated and bounded (~dozens of rows),
    so a Scan beats maintaining a single-partition GSI just to list.
  - get_tag(slug)           — GetItem
  - list_tags_for_deck(d)   — Query PK = DECK#d, begins_with(SK, "TAG#")
  - list_decks_for_tag(s)   — Query TagDeck-Index, GSI1PK = TAG#s
  - list_tags_for_decks([]) — N parallel per-deck Queries. BatchGetItem
    doesn't support begins_with on the sort key, so we fan out. For page-
    render N < ~100 decks this is cheap.

Writes:
  - create_tag(slug, …)     — PutItem with attribute_not_exists(PK)
  - update_tag(slug, …)     — UpdateItem SET only non-None args
  - delete_tag(slug)        — DeleteItem on the canonical row + Query the
    reverse GSI + BatchWriteItem(delete) all DECK#*/TAG#slug mirror rows.
    BatchWriteItem caps at 25 per call — table.batch_writer() paginates.
  - set_deck_tags(deck, slugs) — diff against current set; BatchWriteItem
    Put new + Delete missing mirror rows. Validates every slug via per-tag
    GetItem first; raises ValueError if any slug doesn't exist.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any

from app.db.dynamo._session import get_shared_resource

_META_SK = "META"
TAG_DECK_INDEX = "TagDeck-Index"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _slug_pk(slug: str) -> str:
    return f"SLUG#{slug}"


def _deck_pk(deck_id: str) -> str:
    return f"DECK#{deck_id}"


def _tag_sk(slug: str) -> str:
    return f"TAG#{slug}"


def _tag_gsi_pk(slug: str) -> str:
    return f"TAG#{slug}"


def _deck_gsi_sk(deck_id: str) -> str:
    return f"DECK#{deck_id}"


def _item_to_tag(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "slug": item["slug"],
        "display_name": item.get("display_name", ""),
        "description": item.get("description"),
        "color": item.get("color"),
        "created_at": item.get("created_at"),
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


class DynamoTagRepository:
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

    # ── Canonical tags ────────────────────────────────────────────────────

    async def list_tags(self) -> list[dict[str, Any]]:
        items = await _paginate_scan(
            self._table,
            FilterExpression="SK = :meta",
            ExpressionAttributeValues={":meta": _META_SK},
        )
        tags = [_item_to_tag(it) for it in items if it.get("SK") == _META_SK]
        tags.sort(key=lambda t: t.get("slug") or "")
        return tags

    async def get_tag(self, slug: str) -> dict[str, Any] | None:
        resp = await self._table.get_item(Key={"PK": _slug_pk(slug), "SK": _META_SK})
        item = resp.get("Item")
        return _item_to_tag(item) if item else None

    async def create_tag(
        self,
        slug: str,
        display_name: str,
        description: str | None = None,
        color: str | None = None,
    ) -> dict[str, Any]:
        now = _now_iso()
        item: dict[str, Any] = {
            "PK": _slug_pk(slug),
            "SK": _META_SK,
            "slug": slug,
            "display_name": display_name,
            "created_at": now,
        }
        if description is not None:
            item["description"] = description
        if color is not None:
            item["color"] = color
        try:
            await self._table.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(PK)",
            )
        except Exception as exc:  # noqa: BLE001 — botocore wraps the conditional error
            if "ConditionalCheckFailed" in str(type(exc).__name__) or "ConditionalCheckFailed" in str(exc):
                raise ValueError(f"tag already exists: {slug}") from exc
            raise
        return {
            "slug": slug,
            "display_name": display_name,
            "description": description,
            "color": color,
            "created_at": now,
        }

    async def update_tag(
        self,
        slug: str,
        *,
        display_name: str | None = None,
        description: str | None = None,
        color: str | None = None,
    ) -> dict[str, Any] | None:
        existing = await self.get_tag(slug)
        if not existing:
            return None
        sets: list[str] = []
        names: dict[str, str] = {}
        values: dict[str, Any] = {}
        if display_name is not None:
            sets.append("#dn = :dn")
            names["#dn"] = "display_name"
            values[":dn"] = display_name
        if description is not None:
            sets.append("#desc = :desc")
            names["#desc"] = "description"
            values[":desc"] = description
        if color is not None:
            sets.append("#col = :col")
            names["#col"] = "color"
            values[":col"] = color
        if sets:
            await self._table.update_item(
                Key={"PK": _slug_pk(slug), "SK": _META_SK},
                UpdateExpression="SET " + ", ".join(sets),
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
        return await self.get_tag(slug)

    async def delete_tag(self, slug: str) -> bool:
        existing = await self.get_tag(slug)
        if not existing:
            return False

        # Delete the canonical row first so even a partial cascade leaves the
        # tag unusable from the read paths that key off PK=SLUG#x.
        await self._table.delete_item(Key={"PK": _slug_pk(slug), "SK": _META_SK})

        # Cascade: find every deck-tag mirror row that points at this slug.
        mirror_rows = await _paginate_query(
            self._table,
            IndexName=TAG_DECK_INDEX,
            KeyConditionExpression="GSI1PK = :gpk",
            ExpressionAttributeValues={":gpk": _tag_gsi_pk(slug)},
            ProjectionExpression="PK, SK",
        )
        async with self._table.batch_writer() as batch:
            for row in mirror_rows:
                await batch.delete_item(Key={"PK": row["PK"], "SK": row["SK"]})
        return True

    # ── Deck ↔ tag join ──────────────────────────────────────────────────

    async def list_tags_for_deck(self, deck_id: str) -> list[str]:
        items = await _paginate_query(
            self._table,
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            ExpressionAttributeValues={":pk": _deck_pk(deck_id), ":sk": "TAG#"},
            ProjectionExpression="SK",
        )
        slugs = [it["SK"][len("TAG#") :] for it in items]
        slugs.sort()
        return slugs

    async def list_tags_for_decks(self, deck_ids: list[str]) -> dict[str, list[str]]:
        if not deck_ids:
            return {}
        # Dynamo BatchGetItem doesn't support begins_with on the sort key, so
        # we fan out one Query per deck. Page-size N ≤ ~100 → acceptable.
        results = await asyncio.gather(
            *[self.list_tags_for_deck(d) for d in deck_ids],
            return_exceptions=False,
        )
        return dict(zip(deck_ids, results))

    async def list_decks_for_tag(self, slug: str) -> list[str]:
        items = await _paginate_query(
            self._table,
            IndexName=TAG_DECK_INDEX,
            KeyConditionExpression="GSI1PK = :gpk",
            ExpressionAttributeValues={":gpk": _tag_gsi_pk(slug)},
            ProjectionExpression="GSI1SK",
        )
        deck_ids = [it["GSI1SK"][len("DECK#") :] for it in items]
        deck_ids.sort()
        return deck_ids

    async def set_deck_tags(self, deck_id: str, tag_slugs: list[str]) -> None:
        # Dedup while preserving the caller's order.
        seen: set[str] = set()
        desired: list[str] = []
        for slug in tag_slugs:
            if slug not in seen:
                seen.add(slug)
                desired.append(slug)

        # Validate every slug exists. The protocol contract says callers
        # should validate first, but we double-check to avoid leaving orphan
        # mirror rows pointing at a tag that was never (or no longer) in the
        # canonical dictionary.
        if desired:
            missing: list[str] = []
            existing = await asyncio.gather(*[self.get_tag(s) for s in desired])
            for slug, tag in zip(desired, existing):
                if tag is None:
                    missing.append(slug)
            if missing:
                raise ValueError(f"unknown tag slug(s): {', '.join(sorted(missing))}")

        # Diff vs current mirror rows.
        current = set(await self.list_tags_for_deck(deck_id))
        desired_set = set(desired)
        to_add = desired_set - current
        to_remove = current - desired_set

        # table.batch_writer() handles the 25-item BatchWriteItem cap for us.
        async with self._table.batch_writer() as batch:
            for slug in to_add:
                await batch.put_item(
                    Item={
                        "PK": _deck_pk(deck_id),
                        "SK": _tag_sk(slug),
                        "GSI1PK": _tag_gsi_pk(slug),
                        "GSI1SK": _deck_gsi_sk(deck_id),
                        "deck_id": deck_id,
                        "tag_slug": slug,
                    }
                )
            for slug in to_remove:
                await batch.delete_item(
                    Key={"PK": _deck_pk(deck_id), "SK": _tag_sk(slug)}
                )
