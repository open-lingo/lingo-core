"""SQLite-backed community repository (local development).

Implements the full CommunityRepository protocol against a single SQLite file.
Each domain lives in its own table; thread/post denormalized vote counts are
recomputed inside ``upsert_vote`` / ``remove_vote`` so list reads stay cheap.

Schema is created lazily on ``connect()`` and seeded with the default category
list (general / features / bugs / tips / content) the React forum router
expects out of the box.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import aiosqlite

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS community_categories (
    id              TEXT PRIMARY KEY,
    slug            TEXT NOT NULL UNIQUE,
    name_key        TEXT NOT NULL,
    description_key TEXT NOT NULL,
    sort_order      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_community_categories_sort
    ON community_categories (sort_order);

CREATE TABLE IF NOT EXISTS community_tags (
    id          TEXT PRIMARY KEY,
    slug        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    color       TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS community_threads (
    id              TEXT PRIMARY KEY,
    category_id     TEXT NOT NULL,
    author_id       TEXT NOT NULL,
    author_name     TEXT NOT NULL DEFAULT 'User',
    title           TEXT NOT NULL,
    excerpt         TEXT NOT NULL DEFAULT '',
    body_markdown   TEXT NOT NULL DEFAULT '',
    reply_count     INTEGER NOT NULL DEFAULT 0,
    upvote_count    INTEGER NOT NULL DEFAULT 0,
    downvote_count  INTEGER NOT NULL DEFAULT 0,
    view_count      INTEGER NOT NULL DEFAULT 0,
    is_pinned       INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'open',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_community_threads_category
    ON community_threads (category_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_community_threads_updated
    ON community_threads (updated_at DESC);

CREATE TABLE IF NOT EXISTS community_posts (
    id              TEXT PRIMARY KEY,
    thread_id       TEXT NOT NULL,
    parent_id       TEXT,
    author_id       TEXT NOT NULL,
    author_name     TEXT NOT NULL DEFAULT 'User',
    body_markdown   TEXT NOT NULL DEFAULT '',
    upvote_count    INTEGER NOT NULL DEFAULT 0,
    downvote_count  INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_community_posts_thread
    ON community_posts (thread_id, created_at);

CREATE TABLE IF NOT EXISTS community_thread_tags (
    thread_id   TEXT NOT NULL,
    tag_id      TEXT NOT NULL,
    PRIMARY KEY (thread_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_community_thread_tags_tag
    ON community_thread_tags (tag_id);

CREATE TABLE IF NOT EXISTS community_content_links (
    id            TEXT PRIMARY KEY,
    thread_id     TEXT NOT NULL,
    content_type  TEXT NOT NULL,
    content_id    TEXT NOT NULL,
    language_id   TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_community_content_links_thread
    ON community_content_links (thread_id);
CREATE INDEX IF NOT EXISTS idx_community_content_links_lookup
    ON community_content_links (content_type, content_id);

CREATE TABLE IF NOT EXISTS community_votes (
    user_id      TEXT NOT NULL,
    target_type  TEXT NOT NULL,
    target_id    TEXT NOT NULL,
    value        INTEGER NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (user_id, target_type, target_id)
);
CREATE INDEX IF NOT EXISTS idx_community_votes_target
    ON community_votes (target_type, target_id);

CREATE TABLE IF NOT EXISTS community_addons (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    language_id   TEXT NOT NULL DEFAULT '',
    name          TEXT NOT NULL DEFAULT '',
    description   TEXT NOT NULL DEFAULT '',
    source_url    TEXT,
    author_id     TEXT NOT NULL,
    upvote_count  INTEGER NOT NULL DEFAULT 0,
    item_count    INTEGER,
    status        TEXT NOT NULL DEFAULT 'draft',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_community_addons_kind
    ON community_addons (kind, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_community_addons_author
    ON community_addons (author_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS community_markdown (
    key            TEXT PRIMARY KEY,
    content        TEXT NOT NULL DEFAULT '',
    content_type   TEXT,
    metadata_json  TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
"""

# Default categories — match MockCommunityRepository so the React client
# sees the same surface regardless of backend.
_DEFAULT_CATEGORIES = [
    ("c1", "general", "forum.categoryGeneral", "forum.categoryGeneralDesc", 0),
    ("c2", "features", "forum.categoryFeatures", "forum.categoryFeaturesDesc", 1),
    ("c3", "bugs", "forum.categoryBugs", "forum.categoryBugsDesc", 2),
    ("c4", "tips", "forum.categoryTips", "forum.categoryTipsDesc", 3),
    ("c5", "content", "forum.categoryContent", "forum.categoryContentDesc", 4),
]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _row_to_dict(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def _bool(v: Any) -> bool:
    return bool(v) if v is not None else False


def _thread_row(row: aiosqlite.Row) -> dict[str, Any]:
    d = dict(row)
    d["is_pinned"] = _bool(d.get("is_pinned"))
    return d


def _addon_row(row: aiosqlite.Row) -> dict[str, Any]:
    return dict(row)


def _markdown_row(row: aiosqlite.Row) -> dict[str, Any]:
    d = dict(row)
    raw = d.pop("metadata_json", None)
    try:
        d["metadata"] = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        d["metadata"] = {}
    return d


class SqliteCommunityRepository:
    """SQLite implementation of CommunityRepository."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_INIT_SQL)
        await self._seed_categories()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    def _conn(self) -> aiosqlite.Connection:
        assert self._db is not None, "call connect() first"
        return self._db

    async def _seed_categories(self) -> None:
        cur = await self._conn().execute("SELECT COUNT(*) AS n FROM community_categories")
        row = await cur.fetchone()
        if row and int(row["n"]) > 0:
            return
        now = _now_iso()
        await self._conn().executemany(
            """INSERT OR IGNORE INTO community_categories
                   (id, slug, name_key, description_key, sort_order, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [(cid, slug, name_key, desc_key, order, now, now) for cid, slug, name_key, desc_key, order in _DEFAULT_CATEGORIES],
        )
        await self._conn().commit()

    # ── Categories ───────────────────────────────────────────────────────────

    async def list_categories(self) -> list[dict[str, Any]]:
        cur = await self._conn().execute(
            """SELECT id, slug, name_key, description_key, sort_order, created_at, updated_at
               FROM community_categories ORDER BY sort_order ASC, id ASC"""
        )
        return [dict(r) for r in await cur.fetchall()]

    async def get_category_by_id(self, category_id: str) -> dict[str, Any] | None:
        cur = await self._conn().execute(
            """SELECT id, slug, name_key, description_key, sort_order, created_at, updated_at
               FROM community_categories WHERE id = ?""",
            (category_id,),
        )
        return _row_to_dict(await cur.fetchone())

    async def get_category_by_slug(self, slug: str) -> dict[str, Any] | None:
        cur = await self._conn().execute(
            """SELECT id, slug, name_key, description_key, sort_order, created_at, updated_at
               FROM community_categories WHERE slug = ?""",
            (slug,),
        )
        return _row_to_dict(await cur.fetchone())

    # ── Tags ─────────────────────────────────────────────────────────────────

    async def list_tags(self) -> list[dict[str, Any]]:
        cur = await self._conn().execute("SELECT id, slug, name, color, created_at FROM community_tags ORDER BY name ASC")
        return [dict(r) for r in await cur.fetchall()]

    async def get_tag_by_id(self, tag_id: str) -> dict[str, Any] | None:
        cur = await self._conn().execute(
            "SELECT id, slug, name, color, created_at FROM community_tags WHERE id = ?",
            (tag_id,),
        )
        return _row_to_dict(await cur.fetchone())

    async def create_tag(self, tag: dict[str, Any]) -> dict[str, Any]:
        tid = str(uuid.uuid4())
        now = _now_iso()
        await self._conn().execute(
            """INSERT INTO community_tags (id, slug, name, color, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (tid, tag.get("slug", tid), tag.get("name", ""), tag.get("color"), now),
        )
        await self._conn().commit()
        created = await self.get_tag_by_id(tid)
        assert created is not None
        return created

    # ── Threads ──────────────────────────────────────────────────────────────

    async def create_thread(self, thread: dict[str, Any]) -> dict[str, Any]:
        tid = str(uuid.uuid4())
        now = _now_iso()
        title = thread["title"]
        excerpt = thread.get("excerpt") or title[:200]
        await self._conn().execute(
            """INSERT INTO community_threads (
                   id, category_id, author_id, author_name,
                   title, excerpt, body_markdown,
                   reply_count, upvote_count, downvote_count, view_count,
                   is_pinned, status, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, ?, ?, ?, ?)""",
            (
                tid,
                thread["category_id"],
                thread["author_id"],
                thread.get("author_name", "User"),
                title,
                excerpt,
                thread.get("body_markdown", ""),
                1 if thread.get("is_pinned") else 0,
                thread.get("status", "open"),
                now,
                now,
            ),
        )
        await self._conn().commit()
        created = await self.get_thread_by_id(tid)
        assert created is not None
        return created

    async def get_thread_by_id(self, thread_id: str) -> dict[str, Any] | None:
        cur = await self._conn().execute("SELECT * FROM community_threads WHERE id = ?", (thread_id,))
        row = await cur.fetchone()
        return _thread_row(row) if row else None

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
        clauses: list[str] = []
        params: list[Any] = []
        joins = ""
        if category_id:
            clauses.append("t.category_id = ?")
            params.append(category_id)
        if tag_id:
            joins += " JOIN community_thread_tags tt ON tt.thread_id = t.id"
            clauses.append("tt.tag_id = ?")
            params.append(tag_id)
        if content_type and content_id:
            joins += " JOIN community_content_links cl ON cl.thread_id = t.id"
            clauses.append("cl.content_type = ? AND cl.content_id = ?")
            params.extend([content_type, content_id])
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        if sort == "new":
            order = " ORDER BY t.updated_at DESC"
        else:
            # 'hot' — pinned first, then ranked by upvotes - downvotes + 2*replies.
            order = " ORDER BY t.is_pinned DESC, (t.upvote_count - t.downvote_count + t.reply_count * 2) DESC, t.updated_at DESC"
        sql = f"SELECT t.* FROM community_threads t{joins}{where}{order} LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        cur = await self._conn().execute(sql, params)
        return [_thread_row(r) for r in await cur.fetchall()]

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
        params: list[Any] = []
        for k, v in patch.items():
            if k not in allowed:
                continue
            if k == "is_pinned":
                v = 1 if v else 0
            sets.append(f"{k} = ?")
            params.append(v)
        if not sets:
            return existing
        sets.append("updated_at = ?")
        params.append(_now_iso())
        params.append(thread_id)
        await self._conn().execute(f"UPDATE community_threads SET {', '.join(sets)} WHERE id = ?", params)
        await self._conn().commit()
        updated = await self.get_thread_by_id(thread_id)
        assert updated is not None
        return updated

    async def increment_thread_views(self, thread_id: str) -> None:
        await self._conn().execute(
            "UPDATE community_threads SET view_count = view_count + 1 WHERE id = ?",
            (thread_id,),
        )
        await self._conn().commit()

    # ── Posts ────────────────────────────────────────────────────────────────

    async def create_post(self, post: dict[str, Any]) -> dict[str, Any]:
        pid = str(uuid.uuid4())
        now = _now_iso()
        await self._conn().execute(
            """INSERT INTO community_posts (
                   id, thread_id, parent_id, author_id, author_name,
                   body_markdown, upvote_count, downvote_count,
                   created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, ?)""",
            (
                pid,
                post["thread_id"],
                post.get("parent_id"),
                post["author_id"],
                post.get("author_name", "User"),
                post.get("body_markdown", ""),
                now,
                now,
            ),
        )
        # Bump parent thread reply_count + updated_at so 'new' sort reflects activity.
        await self._conn().execute(
            """UPDATE community_threads
               SET reply_count = reply_count + 1, updated_at = ?
               WHERE id = ?""",
            (now, post["thread_id"]),
        )
        await self._conn().commit()
        created = await self.get_post_by_id(pid)
        assert created is not None
        return created

    async def get_post_by_id(self, post_id: str) -> dict[str, Any] | None:
        cur = await self._conn().execute("SELECT * FROM community_posts WHERE id = ?", (post_id,))
        return _row_to_dict(await cur.fetchone())

    async def list_posts_by_thread(
        self,
        thread_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        cur = await self._conn().execute(
            """SELECT * FROM community_posts
               WHERE thread_id = ? ORDER BY created_at ASC LIMIT ? OFFSET ?""",
            (thread_id, limit, offset),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def update_post(self, post_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        existing = await self.get_post_by_id(post_id)
        if existing is None:
            raise LookupError(f"Post {post_id} not found")
        allowed = {"body_markdown", "upvote_count", "downvote_count", "author_name"}
        sets: list[str] = []
        params: list[Any] = []
        for k, v in patch.items():
            if k in allowed:
                sets.append(f"{k} = ?")
                params.append(v)
        if not sets:
            return existing
        sets.append("updated_at = ?")
        params.append(_now_iso())
        params.append(post_id)
        await self._conn().execute(f"UPDATE community_posts SET {', '.join(sets)} WHERE id = ?", params)
        await self._conn().commit()
        updated = await self.get_post_by_id(post_id)
        assert updated is not None
        return updated

    # ── Thread ↔ tag junction ────────────────────────────────────────────────

    async def set_thread_tags(self, thread_id: str, tag_ids: list[str]) -> None:
        await self._conn().execute("DELETE FROM community_thread_tags WHERE thread_id = ?", (thread_id,))
        if tag_ids:
            await self._conn().executemany(
                "INSERT OR IGNORE INTO community_thread_tags (thread_id, tag_id) VALUES (?, ?)",
                [(thread_id, tid) for tid in tag_ids],
            )
        await self._conn().commit()

    async def get_thread_tag_ids(self, thread_id: str) -> list[str]:
        cur = await self._conn().execute(
            "SELECT tag_id FROM community_thread_tags WHERE thread_id = ?",
            (thread_id,),
        )
        return [r["tag_id"] for r in await cur.fetchall()]

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
        await self._conn().execute(
            """INSERT INTO community_content_links
                   (id, thread_id, content_type, content_id, language_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (link_id, thread_id, content_type, content_id, language_id, now),
        )
        await self._conn().commit()
        cur = await self._conn().execute(
            """SELECT id, thread_id, content_type, content_id, language_id, created_at
               FROM community_content_links WHERE id = ?""",
            (link_id,),
        )
        row = await cur.fetchone()
        assert row is not None
        return dict(row)

    async def list_content_links_by_thread(self, thread_id: str) -> list[dict[str, Any]]:
        cur = await self._conn().execute(
            """SELECT id, thread_id, content_type, content_id, language_id, created_at
               FROM community_content_links WHERE thread_id = ?
               ORDER BY created_at ASC""",
            (thread_id,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def list_threads_by_content(
        self,
        content_type: str,
        content_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        cur = await self._conn().execute(
            """SELECT t.* FROM community_threads t
               JOIN community_content_links cl ON cl.thread_id = t.id
               WHERE cl.content_type = ? AND cl.content_id = ?
               ORDER BY t.updated_at DESC LIMIT ?""",
            (content_type, content_id, limit),
        )
        return [_thread_row(r) for r in await cur.fetchall()]

    # ── Votes ────────────────────────────────────────────────────────────────

    async def upsert_vote(
        self,
        user_id: str,
        target_type: str,
        target_id: str,
        value: int,
    ) -> None:
        now = _now_iso()
        await self._conn().execute(
            """INSERT INTO community_votes
                   (user_id, target_type, target_id, value, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id, target_type, target_id) DO UPDATE SET
                   value = excluded.value""",
            (user_id, target_type, target_id, value, now),
        )
        await self._conn().commit()
        await self._recompute_vote_counts(target_type, target_id)

    async def get_user_vote(
        self,
        user_id: str,
        target_type: str,
        target_id: str,
    ) -> int | None:
        cur = await self._conn().execute(
            """SELECT value FROM community_votes
               WHERE user_id = ? AND target_type = ? AND target_id = ?""",
            (user_id, target_type, target_id),
        )
        row = await cur.fetchone()
        return int(row["value"]) if row else None

    async def remove_vote(
        self,
        user_id: str,
        target_type: str,
        target_id: str,
    ) -> None:
        await self._conn().execute(
            """DELETE FROM community_votes
               WHERE user_id = ? AND target_type = ? AND target_id = ?""",
            (user_id, target_type, target_id),
        )
        await self._conn().commit()
        await self._recompute_vote_counts(target_type, target_id)

    async def _recompute_vote_counts(self, target_type: str, target_id: str) -> None:
        cur = await self._conn().execute(
            """SELECT
                   COALESCE(SUM(CASE WHEN value = 1 THEN 1 ELSE 0 END), 0)  AS up,
                   COALESCE(SUM(CASE WHEN value = -1 THEN 1 ELSE 0 END), 0) AS down
               FROM community_votes WHERE target_type = ? AND target_id = ?""",
            (target_type, target_id),
        )
        row = await cur.fetchone()
        up = int(row["up"]) if row else 0
        down = int(row["down"]) if row else 0
        if target_type == "thread":
            await self._conn().execute(
                """UPDATE community_threads
                   SET upvote_count = ?, downvote_count = ? WHERE id = ?""",
                (up, down, target_id),
            )
        elif target_type == "post":
            await self._conn().execute(
                """UPDATE community_posts
                   SET upvote_count = ?, downvote_count = ? WHERE id = ?""",
                (up, down, target_id),
            )
        await self._conn().commit()

    # ── Addons ───────────────────────────────────────────────────────────────

    async def create_addon(self, addon: dict[str, Any]) -> dict[str, Any]:
        aid = str(uuid.uuid4())
        now = _now_iso()
        await self._conn().execute(
            """INSERT INTO community_addons (
                   id, kind, language_id, name, description, source_url,
                   author_id, upvote_count, item_count, status,
                   created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)""",
            (
                aid,
                addon.get("kind", "course"),
                addon.get("language_id", ""),
                addon.get("name", ""),
                addon.get("description", ""),
                addon.get("source_url"),
                addon["author_id"],
                addon.get("item_count"),
                addon.get("status", "draft"),
                now,
                now,
            ),
        )
        await self._conn().commit()
        created = await self.get_addon_by_id(aid)
        assert created is not None
        return created

    async def get_addon_by_id(self, addon_id: str) -> dict[str, Any] | None:
        cur = await self._conn().execute("SELECT * FROM community_addons WHERE id = ?", (addon_id,))
        row = await cur.fetchone()
        return _addon_row(row) if row else None

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
        clauses: list[str] = []
        params: list[Any] = []
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if language_id:
            clauses.append("language_id = ?")
            params.append(language_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if author_id:
            clauses.append("author_id = ?")
            params.append(author_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM community_addons{where} ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        cur = await self._conn().execute(sql, params)
        return [_addon_row(r) for r in await cur.fetchall()]

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
        params: list[Any] = []
        for k, v in patch.items():
            if k in allowed:
                sets.append(f"{k} = ?")
                params.append(v)
        if not sets:
            return existing
        sets.append("updated_at = ?")
        params.append(_now_iso())
        params.append(addon_id)
        await self._conn().execute(f"UPDATE community_addons SET {', '.join(sets)} WHERE id = ?", params)
        await self._conn().commit()
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
        meta_json = json.dumps(metadata or {})
        await self._conn().execute(
            """INSERT INTO community_markdown
                   (key, content, content_type, metadata_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                   content = excluded.content,
                   content_type = excluded.content_type,
                   metadata_json = excluded.metadata_json,
                   updated_at = excluded.updated_at""",
            (key, content, content_type, meta_json, now, now),
        )
        await self._conn().commit()
        stored = await self.get_markdown(key)
        assert stored is not None
        return stored

    async def get_markdown(self, key: str) -> dict[str, Any] | None:
        cur = await self._conn().execute(
            """SELECT key, content, content_type, metadata_json, created_at, updated_at
               FROM community_markdown WHERE key = ?""",
            (key,),
        )
        row = await cur.fetchone()
        return _markdown_row(row) if row else None

    async def delete_markdown(self, key: str) -> bool:
        cur = await self._conn().execute("DELETE FROM community_markdown WHERE key = ?", (key,))
        await self._conn().commit()
        return (cur.rowcount or 0) > 0
