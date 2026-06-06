"""DynamoDB-backed community repository — 5-table production split.

Five physical tables (matches ``lingo-infra/main.tf``):

  ``lingo_community_threads``   — threads + tags + categories + content links
  ``lingo_community_posts``     — posts/replies
  ``lingo_community_votes``     — per-(user, target) vote rows
  ``lingo_community_addons``    — community addon metadata
  ``lingo_community_markdown``  — raw markdown blobs keyed by path

The threads table holds the high-cardinality forum graph (threads, tags,
categories, thread↔tag mirror rows, content links + their reverse
lookup mirror rows) because the GSI ``CategoryUpdated-Index`` already
gives us category-scoped time-ordering for the listing surface.

PK / SK conventions per table
-----------------------------

threads table:
  Thread row             PK=``THREAD#<id>``       SK=``META``
                         GSI1PK=``CAT#<cat_id>``  GSI1SK=``<updated_at>#<id>``
  Category row           PK=``CATEGORY#<id>``     SK=``META``
  Tag row                PK=``TAG#<id>``          SK=``META``
  Thread→tag mirror      PK=``THREAD#<id>``       SK=``TT#<tag_id>``
  Tag→thread mirror      PK=``TAGT#<tag_id>``     SK=``THREAD#<thread_id>``
  Thread content link    PK=``THREAD#<id>``       SK=``LINK#<link_id>``
  Content→thread mirror  PK=``CONTENT#<t>#<id>``  SK=``THREAD#<updated_at>#<thread_id>``

posts table:
  Chronological row      PK=``THREAD#<thread_id>`` SK=``POST#<created_at>#<id>``
  Direct-lookup row      PK=``POST#<id>``         SK=``META``  (full post)

votes table:
  PK=``<TARGET_TYPE>#<target_id>``  SK=``USER#<user_id>``  (attribute: value)

addons table:
  PK=``ADDON#<id>``       SK=``META``
  GSI1PK=``KIND#<kind>``   GSI1SK=``<updated_at>#<id>``   (KindUpdated-Index)
  GSI2PK=``AUTHOR#<auth>`` GSI2SK=``<updated_at>#<id>``   (AuthorUpdated-Index)

markdown table:
  PK=``MD#<key>``  SK=``META``  (attributes: content, content_type, metadata_json, …)

Behaviour matches ``SqliteCommunityRepository`` so the routers stay
backend-agnostic. Categories are seeded on ``connect()`` (idempotent
via ``attribute_not_exists`` on each PutItem).

The chronological ``POST#<created_at>#<id>`` SK lets us list a thread's
posts via Query(PK=THREAD#tid, begins_with(SK, "POST#")) — no GSI
needed. The direct-lookup ``POST#<id>``/``META`` row keeps
``get_post_by_id`` O(1). Two writes per post is the cost of two read
patterns without a third index.

Numeric note: DynamoDB returns numbers as ``Decimal``; we coerce ints
back via ``_dec`` when materialising rows so the FastAPI router and
Pydantic models see the same types as the SQLite backend.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from botocore.exceptions import ClientError

from app.db.dynamo._session import get_shared_resource

_META = "META"

_THREAD_PK = "THREAD#"
_CATEGORY_PK = "CATEGORY#"
_TAG_PK = "TAG#"
_TAGT_PK = "TAGT#"
_CONTENT_PK = "CONTENT#"
_TT_SK = "TT#"
_LINK_SK = "LINK#"
_THREAD_SK = "THREAD#"
_POST_SK = "POST#"
_POST_PK = "POST#"
_USER_SK = "USER#"
_ADDON_PK = "ADDON#"
_KIND_GSI = "KIND#"
_AUTHOR_GSI = "AUTHOR#"
_MD_PK = "MD#"

CATEGORY_UPDATED_INDEX = "CategoryUpdated-Index"
KIND_UPDATED_INDEX = "KindUpdated-Index"
AUTHOR_UPDATED_INDEX = "AuthorUpdated-Index"

# Default categories — must match SqliteCommunityRepository so the React
# forum router sees the same surface regardless of backend.
_DEFAULT_CATEGORIES = [
    ("c1", "general", "forum.categoryGeneral", "forum.categoryGeneralDesc", 0),
    ("c2", "features", "forum.categoryFeatures", "forum.categoryFeaturesDesc", 1),
    ("c3", "bugs", "forum.categoryBugs", "forum.categoryBugsDesc", 2),
    ("c4", "tips", "forum.categoryTips", "forum.categoryTipsDesc", 3),
    ("c5", "content", "forum.categoryContent", "forum.categoryContentDesc", 4),
]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _dec(v: Any) -> int:
    if v is None:
        return 0
    if isinstance(v, Decimal):
        return int(v)
    return int(v)


async def _paginate_query(table: Any, **kwargs: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    resp = await table.query(**kwargs)
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = await table.query(**kwargs, ExclusiveStartKey=resp["LastEvaluatedKey"])
        items.extend(resp.get("Items", []))
    return items


async def _paginate_scan(table: Any, **kwargs: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    resp = await table.scan(**kwargs)
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = await table.scan(**kwargs, ExclusiveStartKey=resp["LastEvaluatedKey"])
        items.extend(resp.get("Items", []))
    return items


# ── Item ↔ dict adapters ─────────────────────────────────────────────────────


def _category_from_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "slug": item["slug"],
        "name_key": item.get("name_key", ""),
        "description_key": item.get("description_key", ""),
        "sort_order": _dec(item.get("sort_order", 0)),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }


def _tag_from_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "slug": item.get("slug", item["id"]),
        "name": item.get("name", ""),
        "color": item.get("color"),
        "created_at": item.get("created_at"),
    }


def _thread_from_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "category_id": item.get("category_id", ""),
        "author_id": item.get("author_id", ""),
        "author_name": item.get("author_name") or "User",
        "title": item.get("title", ""),
        "excerpt": item.get("excerpt", ""),
        "body_markdown": item.get("body_markdown", ""),
        "reply_count": _dec(item.get("reply_count", 0)),
        "upvote_count": _dec(item.get("upvote_count", 0)),
        "downvote_count": _dec(item.get("downvote_count", 0)),
        "view_count": _dec(item.get("view_count", 0)),
        "is_pinned": bool(item.get("is_pinned", False)),
        "status": item.get("status") or "open",
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }


def _post_from_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "thread_id": item.get("thread_id", ""),
        "parent_id": item.get("parent_id"),
        "author_id": item.get("author_id", ""),
        "author_name": item.get("author_name") or "User",
        "body_markdown": item.get("body_markdown", ""),
        "upvote_count": _dec(item.get("upvote_count", 0)),
        "downvote_count": _dec(item.get("downvote_count", 0)),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }


def _addon_from_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "kind": item.get("kind", ""),
        "language_id": item.get("language_id", ""),
        "name": item.get("name", ""),
        "description": item.get("description", ""),
        "source_url": item.get("source_url"),
        "author_id": item.get("author_id", ""),
        "upvote_count": _dec(item.get("upvote_count", 0)),
        "item_count": _dec(item["item_count"]) if item.get("item_count") is not None else None,
        "status": item.get("status") or "draft",
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }


def _markdown_from_item(item: dict[str, Any]) -> dict[str, Any]:
    raw_meta = item.get("metadata_json")
    try:
        metadata = json.loads(raw_meta) if raw_meta else {}
    except json.JSONDecodeError:
        metadata = {}
    return {
        "key": item["key"],
        "content": item.get("content", ""),
        "content_type": item.get("content_type"),
        "metadata": metadata,
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }


def _content_link_from_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "thread_id": item.get("thread_id", ""),
        "content_type": item.get("content_type", ""),
        "content_id": item.get("content_id", ""),
        "language_id": item.get("language_id"),
        "created_at": item.get("created_at"),
    }


def _category_id_to_gsi_pk(category_id: str) -> str:
    return f"CAT#{category_id}"


def _hot_score(t: dict[str, Any]) -> int:
    return _dec(t.get("upvote_count")) - _dec(t.get("downvote_count")) + _dec(t.get("reply_count")) * 2


def _sort_threads(threads: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
    if sort == "new":
        return sorted(threads, key=lambda t: t.get("updated_at") or "", reverse=True)
    # Mirror SQLite: pinned first, then hot score desc, then updated_at desc.
    return sorted(
        threads,
        key=lambda t: (
            1 if t.get("is_pinned") else 0,
            _hot_score(t),
            t.get("updated_at") or "",
        ),
        reverse=True,
    )


class DynamoCommunityRepository:
    """Multi-table DynamoDB community repository.

    Takes a mapping of table names so callers can override per-domain
    in tests without rebuilding the prefix logic::

        DynamoCommunityRepository(
            {
                "threads":  "lingo_community_threads",
                "posts":    "lingo_community_posts",
                "votes":    "lingo_community_votes",
                "addons":   "lingo_community_addons",
                "markdown": "lingo_community_markdown",
            },
            region="us-west-1",
        )
    """

    def __init__(self, table_names: dict[str, str], region: str) -> None:
        missing = {"threads", "posts", "votes", "addons", "markdown"} - table_names.keys()
        if missing:
            raise ValueError(f"DynamoCommunityRepository missing table names: {sorted(missing)}")
        self._table_names = dict(table_names)
        self._region = region
        self._threads_table: Any = None
        self._posts_table: Any = None
        self._votes_table: Any = None
        self._addons_table: Any = None
        self._markdown_table: Any = None

    async def connect(self) -> None:
        resource = await get_shared_resource(self._region)
        self._threads_table, self._posts_table, self._votes_table, self._addons_table, self._markdown_table = (
            await asyncio.gather(
                resource.Table(self._table_names["threads"]),
                resource.Table(self._table_names["posts"]),
                resource.Table(self._table_names["votes"]),
                resource.Table(self._table_names["addons"]),
                resource.Table(self._table_names["markdown"]),
            )
        )
        await self._seed_categories()

    async def close(self) -> None:
        # Shared resource closed via close_shared_resource(); no-op here.
        pass

    # ── Category seeding ─────────────────────────────────────────────────────

    async def _seed_categories(self) -> None:
        """Idempotently insert the default categories. Uses
        ``attribute_not_exists(PK)`` so we don't clobber any later
        edits made via the admin UI.
        """
        now = _now_iso()
        for cid, slug, name_key, desc_key, order in _DEFAULT_CATEGORIES:
            try:
                await self._threads_table.put_item(
                    Item={
                        "PK": f"{_CATEGORY_PK}{cid}",
                        "SK": _META,
                        "entity": "category",
                        "id": cid,
                        "slug": slug,
                        "name_key": name_key,
                        "description_key": desc_key,
                        "sort_order": order,
                        "created_at": now,
                        "updated_at": now,
                    },
                    ConditionExpression="attribute_not_exists(PK)",
                )
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                    raise

    # ── Categories ───────────────────────────────────────────────────────────

    async def list_categories(self) -> list[dict[str, Any]]:
        items = await _paginate_scan(
            self._threads_table,
            FilterExpression="entity = :e",
            ExpressionAttributeValues={":e": "category"},
        )
        cats = [_category_from_item(it) for it in items]
        cats.sort(key=lambda c: (c["sort_order"], c["id"]))
        return cats

    async def get_category_by_id(self, category_id: str) -> dict[str, Any] | None:
        resp = await self._threads_table.get_item(
            Key={"PK": f"{_CATEGORY_PK}{category_id}", "SK": _META},
        )
        item = resp.get("Item")
        return _category_from_item(item) if item else None

    async def get_category_by_slug(self, slug: str) -> dict[str, Any] | None:
        items = await _paginate_scan(
            self._threads_table,
            FilterExpression="entity = :e AND slug = :s",
            ExpressionAttributeValues={":e": "category", ":s": slug},
        )
        if not items:
            return None
        return _category_from_item(items[0])

    # ── Tags ─────────────────────────────────────────────────────────────────

    async def list_tags(self) -> list[dict[str, Any]]:
        items = await _paginate_scan(
            self._threads_table,
            FilterExpression="entity = :e",
            ExpressionAttributeValues={":e": "tag"},
        )
        tags = [_tag_from_item(it) for it in items]
        tags.sort(key=lambda t: t.get("name") or "")
        return tags

    async def get_tag_by_id(self, tag_id: str) -> dict[str, Any] | None:
        resp = await self._threads_table.get_item(
            Key={"PK": f"{_TAG_PK}{tag_id}", "SK": _META},
        )
        item = resp.get("Item")
        return _tag_from_item(item) if item else None

    async def create_tag(self, tag: dict[str, Any]) -> dict[str, Any]:
        tid = str(uuid.uuid4())
        now = _now_iso()
        item: dict[str, Any] = {
            "PK": f"{_TAG_PK}{tid}",
            "SK": _META,
            "entity": "tag",
            "id": tid,
            "slug": tag.get("slug", tid),
            "name": tag.get("name", ""),
            "created_at": now,
        }
        if tag.get("color") is not None:
            item["color"] = tag["color"]
        await self._threads_table.put_item(Item=item)
        return _tag_from_item(item)

    # ── Threads ──────────────────────────────────────────────────────────────

    async def create_thread(self, thread: dict[str, Any]) -> dict[str, Any]:
        tid = str(uuid.uuid4())
        now = _now_iso()
        title = thread["title"]
        excerpt = thread.get("excerpt") or title[:200]
        category_id = thread["category_id"]
        item: dict[str, Any] = {
            "PK": f"{_THREAD_PK}{tid}",
            "SK": _META,
            "entity": "thread",
            "id": tid,
            "category_id": category_id,
            "author_id": thread["author_id"],
            "author_name": thread.get("author_name") or "User",
            "title": title,
            "excerpt": excerpt,
            "body_markdown": thread.get("body_markdown", ""),
            "reply_count": 0,
            "upvote_count": 0,
            "downvote_count": 0,
            "view_count": 0,
            "is_pinned": bool(thread.get("is_pinned", False)),
            "status": thread.get("status") or "open",
            "created_at": now,
            "updated_at": now,
            "GSI1PK": _category_id_to_gsi_pk(category_id),
            "GSI1SK": f"{now}#{tid}",
        }
        await self._threads_table.put_item(Item=item)
        return _thread_from_item(item)

    async def get_thread_by_id(self, thread_id: str) -> dict[str, Any] | None:
        resp = await self._threads_table.get_item(
            Key={"PK": f"{_THREAD_PK}{thread_id}", "SK": _META},
        )
        item = resp.get("Item")
        return _thread_from_item(item) if item else None

    async def list_threads(
        self,
        *,
        category_id: str | None = None,
        tag_id: str | None = None,
        content_type: str | None = None,
        content_id: str | None = None,
        sort: str = "hot",
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        # Pick the most-selective access pattern.
        thread_ids: set[str] | None = None
        candidates: list[dict[str, Any]] = []

        if content_type and content_id:
            # Reverse mirror gives us the targeted thread set in one Query.
            mirror_rows = await _paginate_query(
                self._threads_table,
                KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
                ExpressionAttributeValues={
                    ":pk": f"{_CONTENT_PK}{content_type}#{content_id}",
                    ":sk": _THREAD_SK,
                },
                ProjectionExpression="thread_id",
            )
            thread_ids = {r["thread_id"] for r in mirror_rows if r.get("thread_id")}
            if not thread_ids:
                return []
            candidates = await self._batch_get_threads(list(thread_ids))
        elif tag_id:
            mirror_rows = await _paginate_query(
                self._threads_table,
                KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
                ExpressionAttributeValues={
                    ":pk": f"{_TAGT_PK}{tag_id}",
                    ":sk": _THREAD_SK,
                },
                ProjectionExpression="thread_id",
            )
            thread_ids = {r["thread_id"] for r in mirror_rows if r.get("thread_id")}
            if not thread_ids:
                return []
            candidates = await self._batch_get_threads(list(thread_ids))
        elif category_id:
            items = await _paginate_query(
                self._threads_table,
                IndexName=CATEGORY_UPDATED_INDEX,
                KeyConditionExpression="GSI1PK = :pk",
                ExpressionAttributeValues={":pk": _category_id_to_gsi_pk(category_id)},
                ScanIndexForward=False,
            )
            candidates = [_thread_from_item(it) for it in items]
        else:
            # No filter: fall back to a Scan of thread rows. The forum-home
            # router only ever lands here for unauthenticated browse, which
            # is a low-volume read path.
            items = await _paginate_scan(
                self._threads_table,
                FilterExpression="entity = :e",
                ExpressionAttributeValues={":e": "thread"},
            )
            candidates = [_thread_from_item(it) for it in items]

        # Apply secondary filters (when the primary axis wasn't already a
        # match) — keeps multi-axis filters correct without extra GSIs.
        if category_id:
            candidates = [t for t in candidates if t.get("category_id") == category_id]

        sorted_threads = _sort_threads(candidates, sort)
        return sorted_threads[offset : offset + limit]

    async def _batch_get_threads(self, thread_ids: list[str]) -> list[dict[str, Any]]:
        if not thread_ids:
            return []
        # Fan out — BatchGetItem has a 100-item cap and per-request error
        # handling is fiddly; for forum page sizes (<= ~50) the parallel
        # GetItems pattern is simpler and fast enough.
        rows = await asyncio.gather(*[self.get_thread_by_id(tid) for tid in thread_ids])
        return [r for r in rows if r is not None]

    async def update_thread(self, thread_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        existing = await self.get_thread_by_id(thread_id)
        if existing is None:
            raise LookupError(f"Thread {thread_id} not found")
        allowed = {
            "category_id",
            "author_name",
            "title",
            "excerpt",
            "body_markdown",
            "reply_count",
            "upvote_count",
            "downvote_count",
            "view_count",
            "is_pinned",
            "status",
        }
        sets: list[str] = []
        names: dict[str, str] = {}
        values: dict[str, Any] = {}
        for k, v in patch.items():
            if k not in allowed:
                continue
            placeholder = f":{k}"
            name = f"#{k}"
            if k == "is_pinned":
                v = bool(v)
            sets.append(f"{name} = {placeholder}")
            names[name] = k
            values[placeholder] = v
        if not sets:
            return existing

        now = _now_iso()
        sets.append("#updated_at = :updated_at")
        names["#updated_at"] = "updated_at"
        values[":updated_at"] = now

        # Maintain the GSI1SK denormalisation so re-sorting newest-first
        # reflects the touch. GSI1PK only needs to change if category_id
        # changed in the patch.
        new_category = patch.get("category_id", existing["category_id"])
        sets.append("GSI1SK = :gsk")
        values[":gsk"] = f"{now}#{thread_id}"
        if patch.get("category_id") and new_category != existing["category_id"]:
            sets.append("GSI1PK = :gpk")
            values[":gpk"] = _category_id_to_gsi_pk(new_category)

        await self._threads_table.update_item(
            Key={"PK": f"{_THREAD_PK}{thread_id}", "SK": _META},
            UpdateExpression="SET " + ", ".join(sets),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
        updated = await self.get_thread_by_id(thread_id)
        assert updated is not None
        return updated

    async def increment_thread_views(self, thread_id: str) -> None:
        try:
            await self._threads_table.update_item(
                Key={"PK": f"{_THREAD_PK}{thread_id}", "SK": _META},
                UpdateExpression="ADD view_count :one",
                ConditionExpression="attribute_exists(PK)",
                ExpressionAttributeValues={":one": 1},
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
            # Missing thread — match SQLite's silent no-op.

    # ── Posts ────────────────────────────────────────────────────────────────

    async def create_post(self, post: dict[str, Any]) -> dict[str, Any]:
        pid = str(uuid.uuid4())
        now = _now_iso()
        thread_id = post["thread_id"]
        common: dict[str, Any] = {
            "id": pid,
            "thread_id": thread_id,
            "parent_id": post.get("parent_id"),
            "author_id": post["author_id"],
            "author_name": post.get("author_name") or "User",
            "body_markdown": post.get("body_markdown", ""),
            "upvote_count": 0,
            "downvote_count": 0,
            "created_at": now,
            "updated_at": now,
        }
        # Chronological row (Query by thread).
        chrono_item = {
            **common,
            "PK": f"{_THREAD_PK}{thread_id}",
            "SK": f"{_POST_SK}{now}#{pid}",
        }
        # Direct-lookup row (GetItem by post id).
        direct_item = {
            **common,
            "PK": f"{_POST_PK}{pid}",
            "SK": _META,
        }
        await asyncio.gather(
            self._posts_table.put_item(Item=chrono_item),
            self._posts_table.put_item(Item=direct_item),
        )
        # Bump reply_count + updated_at on the parent thread so the "new"
        # sort reflects activity.
        try:
            await self._threads_table.update_item(
                Key={"PK": f"{_THREAD_PK}{thread_id}", "SK": _META},
                UpdateExpression="ADD reply_count :one SET updated_at = :now, GSI1SK = :gsk",
                ConditionExpression="attribute_exists(PK)",
                ExpressionAttributeValues={
                    ":one": 1,
                    ":now": now,
                    ":gsk": f"{now}#{thread_id}",
                },
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
        return _post_from_item(direct_item)

    async def get_post_by_id(self, post_id: str) -> dict[str, Any] | None:
        resp = await self._posts_table.get_item(
            Key={"PK": f"{_POST_PK}{post_id}", "SK": _META},
        )
        item = resp.get("Item")
        return _post_from_item(item) if item else None

    async def list_posts_by_thread(
        self,
        thread_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        # The chronological SK encodes created_at first → forward scan is
        # already chronological. ``Limit`` from Dynamo doesn't honour our
        # offset, so paginate then slice client-side. For forum-page Ns
        # (≤ ~100) this is fine; if a thread grows past that we'd add a
        # token-based cursor.
        items = await _paginate_query(
            self._posts_table,
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            ExpressionAttributeValues={
                ":pk": f"{_THREAD_PK}{thread_id}",
                ":sk": _POST_SK,
            },
            ScanIndexForward=True,
        )
        sliced = items[offset : offset + limit]
        return [_post_from_item(it) for it in sliced]

    async def update_post(self, post_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        existing = await self.get_post_by_id(post_id)
        if existing is None:
            raise LookupError(f"Post {post_id} not found")
        allowed = {"body_markdown", "upvote_count", "downvote_count", "author_name"}
        sets: list[str] = []
        names: dict[str, str] = {}
        values: dict[str, Any] = {}
        for k, v in patch.items():
            if k in allowed:
                names[f"#{k}"] = k
                values[f":{k}"] = v
                sets.append(f"#{k} = :{k}")
        if not sets:
            return existing

        now = _now_iso()
        names["#updated_at"] = "updated_at"
        values[":updated_at"] = now
        sets.append("#updated_at = :updated_at")

        # Update the direct-lookup row first (it backs get_post_by_id),
        # then mirror into the chronological row so list_posts_by_thread
        # stays consistent.
        thread_id = existing["thread_id"]
        created_at = existing["created_at"]
        await self._posts_table.update_item(
            Key={"PK": f"{_POST_PK}{post_id}", "SK": _META},
            UpdateExpression="SET " + ", ".join(sets),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
        await self._posts_table.update_item(
            Key={"PK": f"{_THREAD_PK}{thread_id}", "SK": f"{_POST_SK}{created_at}#{post_id}"},
            UpdateExpression="SET " + ", ".join(sets),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )

        updated = await self.get_post_by_id(post_id)
        assert updated is not None
        return updated

    # ── Thread ↔ tag mirror ──────────────────────────────────────────────────

    async def set_thread_tags(self, thread_id: str, tag_ids: list[str]) -> None:
        # Read current mirror set so we can diff and delete stale rows.
        current = set(await self.get_thread_tag_ids(thread_id))
        desired_set = set(tag_ids)
        to_add = desired_set - current
        to_remove = current - desired_set

        async with self._threads_table.batch_writer() as batch:
            for tid in to_add:
                # Two mirror rows so we can read both directions.
                await batch.put_item(
                    Item={
                        "PK": f"{_THREAD_PK}{thread_id}",
                        "SK": f"{_TT_SK}{tid}",
                        "thread_id": thread_id,
                        "tag_id": tid,
                    }
                )
                await batch.put_item(
                    Item={
                        "PK": f"{_TAGT_PK}{tid}",
                        "SK": f"{_THREAD_SK}{thread_id}",
                        "thread_id": thread_id,
                        "tag_id": tid,
                    }
                )
            for tid in to_remove:
                await batch.delete_item(
                    Key={"PK": f"{_THREAD_PK}{thread_id}", "SK": f"{_TT_SK}{tid}"},
                )
                await batch.delete_item(
                    Key={"PK": f"{_TAGT_PK}{tid}", "SK": f"{_THREAD_SK}{thread_id}"},
                )

    async def get_thread_tag_ids(self, thread_id: str) -> list[str]:
        items = await _paginate_query(
            self._threads_table,
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            ExpressionAttributeValues={
                ":pk": f"{_THREAD_PK}{thread_id}",
                ":sk": _TT_SK,
            },
            ProjectionExpression="tag_id",
        )
        return [it["tag_id"] for it in items if it.get("tag_id")]

    # ── Content links ────────────────────────────────────────────────────────

    async def add_content_link(
        self,
        thread_id: str,
        content_type: str,
        content_id: str,
        language_id: str | None = None,
    ) -> dict[str, Any]:
        link_id = str(uuid.uuid4())
        now = _now_iso()
        link_row: dict[str, Any] = {
            "PK": f"{_THREAD_PK}{thread_id}",
            "SK": f"{_LINK_SK}{link_id}",
            "id": link_id,
            "thread_id": thread_id,
            "content_type": content_type,
            "content_id": content_id,
            "language_id": language_id,
            "created_at": now,
        }
        # Reverse mirror — lets list_threads_by_content do a single Query.
        mirror_row: dict[str, Any] = {
            "PK": f"{_CONTENT_PK}{content_type}#{content_id}",
            "SK": f"{_THREAD_SK}{now}#{thread_id}",
            "thread_id": thread_id,
            "content_type": content_type,
            "content_id": content_id,
            "language_id": language_id,
            "created_at": now,
        }
        await asyncio.gather(
            self._threads_table.put_item(Item=link_row),
            self._threads_table.put_item(Item=mirror_row),
        )
        return _content_link_from_item(link_row)

    async def list_content_links_by_thread(self, thread_id: str) -> list[dict[str, Any]]:
        items = await _paginate_query(
            self._threads_table,
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            ExpressionAttributeValues={
                ":pk": f"{_THREAD_PK}{thread_id}",
                ":sk": _LINK_SK,
            },
        )
        links = [_content_link_from_item(it) for it in items]
        links.sort(key=lambda link: link.get("created_at") or "")
        return links

    async def list_threads_by_content(
        self,
        content_type: str,
        content_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        mirror_rows = await _paginate_query(
            self._threads_table,
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            ExpressionAttributeValues={
                ":pk": f"{_CONTENT_PK}{content_type}#{content_id}",
                ":sk": _THREAD_SK,
            },
            ScanIndexForward=False,
            ProjectionExpression="thread_id",
        )
        # Mirror SKs encode created_at so the reverse scan is already
        # newest-first; we still de-dup in case a thread was linked
        # twice (the seed pattern in SQLite forbids that, but we don't
        # rely on it).
        seen: set[str] = set()
        ids: list[str] = []
        for r in mirror_rows:
            tid = r.get("thread_id")
            if tid and tid not in seen:
                seen.add(tid)
                ids.append(tid)
            if len(ids) >= limit:
                break
        threads = await self._batch_get_threads(ids)
        # Preserve mirror ordering (already newest-first via mirror SK).
        order = {tid: i for i, tid in enumerate(ids)}
        threads.sort(key=lambda t: order.get(t["id"], 0))
        return threads

    # ── Votes ────────────────────────────────────────────────────────────────

    def _vote_pk(self, target_type: str, target_id: str) -> str:
        return f"{target_type.upper()}#{target_id}"

    async def upsert_vote(
        self,
        user_id: str,
        target_type: str,
        target_id: str,
        value: int,
    ) -> None:
        now = _now_iso()
        await self._votes_table.put_item(
            Item={
                "PK": self._vote_pk(target_type, target_id),
                "SK": f"{_USER_SK}{user_id}",
                "user_id": user_id,
                "target_type": target_type,
                "target_id": target_id,
                "value": int(value),
                "created_at": now,
            }
        )
        await self._recompute_vote_counts(target_type, target_id)

    async def get_user_vote(
        self,
        user_id: str,
        target_type: str,
        target_id: str,
    ) -> int | None:
        resp = await self._votes_table.get_item(
            Key={
                "PK": self._vote_pk(target_type, target_id),
                "SK": f"{_USER_SK}{user_id}",
            },
        )
        item = resp.get("Item")
        if not item:
            return None
        return _dec(item.get("value"))

    async def remove_vote(
        self,
        user_id: str,
        target_type: str,
        target_id: str,
    ) -> None:
        await self._votes_table.delete_item(
            Key={
                "PK": self._vote_pk(target_type, target_id),
                "SK": f"{_USER_SK}{user_id}",
            },
        )
        await self._recompute_vote_counts(target_type, target_id)

    async def _recompute_vote_counts(self, target_type: str, target_id: str) -> None:
        items = await _paginate_query(
            self._votes_table,
            KeyConditionExpression="PK = :pk",
            ExpressionAttributeValues={":pk": self._vote_pk(target_type, target_id)},
            ProjectionExpression="#v",
            ExpressionAttributeNames={"#v": "value"},
        )
        up = sum(1 for it in items if _dec(it.get("value")) == 1)
        down = sum(1 for it in items if _dec(it.get("value")) == -1)

        if target_type == "thread":
            try:
                await self._threads_table.update_item(
                    Key={"PK": f"{_THREAD_PK}{target_id}", "SK": _META},
                    UpdateExpression="SET upvote_count = :u, downvote_count = :d",
                    ConditionExpression="attribute_exists(PK)",
                    ExpressionAttributeValues={":u": up, ":d": down},
                )
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                    raise
        elif target_type == "post":
            # Two rows to keep in sync — direct lookup + chronological mirror.
            direct = await self.get_post_by_id(target_id)
            if direct is None:
                return
            await self._posts_table.update_item(
                Key={"PK": f"{_POST_PK}{target_id}", "SK": _META},
                UpdateExpression="SET upvote_count = :u, downvote_count = :d",
                ExpressionAttributeValues={":u": up, ":d": down},
            )
            await self._posts_table.update_item(
                Key={
                    "PK": f"{_THREAD_PK}{direct['thread_id']}",
                    "SK": f"{_POST_SK}{direct['created_at']}#{target_id}",
                },
                UpdateExpression="SET upvote_count = :u, downvote_count = :d",
                ExpressionAttributeValues={":u": up, ":d": down},
            )

    # ── Addons ───────────────────────────────────────────────────────────────

    async def create_addon(self, addon: dict[str, Any]) -> dict[str, Any]:
        aid = str(uuid.uuid4())
        now = _now_iso()
        kind = addon.get("kind", "course")
        author_id = addon["author_id"]
        item: dict[str, Any] = {
            "PK": f"{_ADDON_PK}{aid}",
            "SK": _META,
            "id": aid,
            "kind": kind,
            "language_id": addon.get("language_id", ""),
            "name": addon.get("name", ""),
            "description": addon.get("description", ""),
            "source_url": addon.get("source_url"),
            "author_id": author_id,
            "upvote_count": 0,
            "item_count": addon.get("item_count"),
            "status": addon.get("status") or "draft",
            "created_at": now,
            "updated_at": now,
            "GSI1PK": f"{_KIND_GSI}{kind}",
            "GSI1SK": f"{now}#{aid}",
            "GSI2PK": f"{_AUTHOR_GSI}{author_id}",
            "GSI2SK": f"{now}#{aid}",
        }
        await self._addons_table.put_item(Item=item)
        return _addon_from_item(item)

    async def get_addon_by_id(self, addon_id: str) -> dict[str, Any] | None:
        resp = await self._addons_table.get_item(
            Key={"PK": f"{_ADDON_PK}{addon_id}", "SK": _META},
        )
        item = resp.get("Item")
        return _addon_from_item(item) if item else None

    async def list_addons(
        self,
        *,
        kind: str | None = None,
        language_id: str | None = None,
        status: str | None = None,
        author_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]]

        # Pick the most-selective indexed dimension first; everything else
        # becomes a client-side filter.
        if author_id:
            items = await _paginate_query(
                self._addons_table,
                IndexName=AUTHOR_UPDATED_INDEX,
                KeyConditionExpression="GSI2PK = :pk",
                ExpressionAttributeValues={":pk": f"{_AUTHOR_GSI}{author_id}"},
                ScanIndexForward=False,
            )
            candidates = [_addon_from_item(it) for it in items]
        elif kind:
            items = await _paginate_query(
                self._addons_table,
                IndexName=KIND_UPDATED_INDEX,
                KeyConditionExpression="GSI1PK = :pk",
                ExpressionAttributeValues={":pk": f"{_KIND_GSI}{kind}"},
                ScanIndexForward=False,
            )
            candidates = [_addon_from_item(it) for it in items]
        else:
            items = await _paginate_scan(self._addons_table)
            candidates = [_addon_from_item(it) for it in items]
            candidates.sort(key=lambda a: a.get("updated_at") or "", reverse=True)

        if kind:
            candidates = [a for a in candidates if a.get("kind") == kind]
        if language_id:
            candidates = [a for a in candidates if a.get("language_id") == language_id]
        if status:
            candidates = [a for a in candidates if a.get("status") == status]
        if author_id:
            candidates = [a for a in candidates if a.get("author_id") == author_id]

        return candidates[offset : offset + limit]

    async def update_addon(self, addon_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        existing = await self.get_addon_by_id(addon_id)
        if existing is None:
            raise LookupError(f"Addon {addon_id} not found")
        allowed = {
            "kind",
            "language_id",
            "name",
            "description",
            "source_url",
            "upvote_count",
            "item_count",
            "status",
        }
        sets: list[str] = []
        names: dict[str, str] = {}
        values: dict[str, Any] = {}
        for k, v in patch.items():
            if k not in allowed:
                continue
            names[f"#{k}"] = k
            values[f":{k}"] = v
            sets.append(f"#{k} = :{k}")
        if not sets:
            return existing

        now = _now_iso()
        names["#updated_at"] = "updated_at"
        values[":updated_at"] = now
        sets.append("#updated_at = :updated_at")

        # Keep GSI SKs fresh so newest-first sort still reflects edits.
        new_kind = patch.get("kind", existing["kind"])
        sets.append("GSI1SK = :gsk1")
        values[":gsk1"] = f"{now}#{addon_id}"
        sets.append("GSI2SK = :gsk2")
        values[":gsk2"] = f"{now}#{addon_id}"
        if "kind" in patch and new_kind != existing["kind"]:
            sets.append("GSI1PK = :gpk1")
            values[":gpk1"] = f"{_KIND_GSI}{new_kind}"

        await self._addons_table.update_item(
            Key={"PK": f"{_ADDON_PK}{addon_id}", "SK": _META},
            UpdateExpression="SET " + ", ".join(sets),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
        updated = await self.get_addon_by_id(addon_id)
        assert updated is not None
        return updated

    # ── Markdown KV ──────────────────────────────────────────────────────────

    async def store_markdown(
        self,
        key: str,
        content: str,
        *,
        content_type: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = _now_iso()
        existing = await self.get_markdown(key)
        created_at = existing["created_at"] if existing else now
        item: dict[str, Any] = {
            "PK": f"{_MD_PK}{key}",
            "SK": _META,
            "key": key,
            "content": content,
            "content_type": content_type,
            "metadata_json": json.dumps(metadata or {}),
            "created_at": created_at,
            "updated_at": now,
        }
        await self._markdown_table.put_item(Item=item)
        return _markdown_from_item(item)

    async def get_markdown(self, key: str) -> dict[str, Any] | None:
        resp = await self._markdown_table.get_item(
            Key={"PK": f"{_MD_PK}{key}", "SK": _META},
        )
        item = resp.get("Item")
        return _markdown_from_item(item) if item else None

    async def delete_markdown(self, key: str) -> bool:
        resp = await self._markdown_table.delete_item(
            Key={"PK": f"{_MD_PK}{key}", "SK": _META},
            ReturnValues="ALL_OLD",
        )
        return bool(resp.get("Attributes"))
