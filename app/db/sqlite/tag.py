"""SQLite-backed tag repository.

Two tables:

  ``tags``       — canonical, admin-curated tag dictionary (slug PK).
  ``deck_tags``  — many-to-many between decks and tags.

Both are created lazily on ``connect()``. ``deck_tags`` carries an index on
each side so the two hot read patterns (list tags for a deck, list decks
for a tag) are O(matches).
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS tags (
    slug         TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    description  TEXT,
    color        TEXT,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deck_tags (
    deck_id   TEXT NOT NULL,
    tag_slug  TEXT NOT NULL,
    PRIMARY KEY (deck_id, tag_slug)
);
CREATE INDEX IF NOT EXISTS idx_deck_tags_deck ON deck_tags (deck_id);
CREATE INDEX IF NOT EXISTS idx_deck_tags_tag  ON deck_tags (tag_slug);
"""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _row_to_tag(row: aiosqlite.Row) -> dict[str, Any]:
    return {
        "slug": row["slug"],
        "display_name": row["display_name"],
        "description": row["description"],
        "color": row["color"],
        "created_at": row["created_at"],
    }


class SqliteTagRepository:
    def __init__(self, db_path: str) -> None:
        self._db_path = str(Path(db_path).resolve())
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_INIT_SQL)

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    def _conn(self) -> aiosqlite.Connection:
        assert self._db is not None, "call connect() first"
        return self._db

    # ── Canonical tags ───────────────────────────────────────────────────────

    async def list_tags(self) -> list[dict[str, Any]]:
        cur = await self._conn().execute(
            "SELECT slug, display_name, description, color, created_at "
            "FROM tags ORDER BY slug"
        )
        return [_row_to_tag(r) for r in await cur.fetchall()]

    async def get_tag(self, slug: str) -> dict[str, Any] | None:
        cur = await self._conn().execute(
            "SELECT slug, display_name, description, color, created_at "
            "FROM tags WHERE slug = ?",
            (slug,),
        )
        row = await cur.fetchone()
        return _row_to_tag(row) if row else None

    async def create_tag(
        self,
        slug: str,
        display_name: str,
        description: str | None = None,
        color: str | None = None,
    ) -> dict[str, Any]:
        now = _now_iso()
        try:
            await self._conn().execute(
                "INSERT INTO tags (slug, display_name, description, color, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (slug, display_name, description, color, now),
            )
            await self._conn().commit()
        except aiosqlite.IntegrityError as exc:
            raise ValueError(f"tag already exists: {slug}") from exc
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
        new_display = display_name if display_name is not None else existing["display_name"]
        new_description = description if description is not None else existing["description"]
        new_color = color if color is not None else existing["color"]
        await self._conn().execute(
            "UPDATE tags SET display_name = ?, description = ?, color = ? WHERE slug = ?",
            (new_display, new_description, new_color, slug),
        )
        await self._conn().commit()
        return await self.get_tag(slug)

    async def delete_tag(self, slug: str) -> bool:
        cur = await self._conn().execute("DELETE FROM tags WHERE slug = ?", (slug,))
        await self._conn().execute("DELETE FROM deck_tags WHERE tag_slug = ?", (slug,))
        await self._conn().commit()
        return (cur.rowcount or 0) > 0

    # ── Deck ↔ tag join ──────────────────────────────────────────────────────

    async def list_tags_for_deck(self, deck_id: str) -> list[str]:
        cur = await self._conn().execute(
            "SELECT tag_slug FROM deck_tags WHERE deck_id = ? ORDER BY tag_slug",
            (deck_id,),
        )
        return [r["tag_slug"] for r in await cur.fetchall()]

    async def list_tags_for_decks(self, deck_ids: list[str]) -> dict[str, list[str]]:
        if not deck_ids:
            return {}
        placeholders = ",".join("?" for _ in deck_ids)
        cur = await self._conn().execute(
            f"SELECT deck_id, tag_slug FROM deck_tags WHERE deck_id IN ({placeholders}) "
            "ORDER BY deck_id, tag_slug",
            deck_ids,
        )
        out: dict[str, list[str]] = {did: [] for did in deck_ids}
        for row in await cur.fetchall():
            out.setdefault(row["deck_id"], []).append(row["tag_slug"])
        return out

    async def list_decks_for_tag(self, slug: str) -> list[str]:
        cur = await self._conn().execute(
            "SELECT deck_id FROM deck_tags WHERE tag_slug = ? ORDER BY deck_id",
            (slug,),
        )
        return [r["deck_id"] for r in await cur.fetchall()]

    async def set_deck_tags(self, deck_id: str, tag_slugs: list[str]) -> None:
        # Replace strategy — wipe and re-insert. Cheap at canonical-tag scale
        # (a deck typically carries ≤ 10 tags) and avoids set-diff bugs.
        await self._conn().execute("DELETE FROM deck_tags WHERE deck_id = ?", (deck_id,))
        seen: set[str] = set()
        rows: list[tuple[str, str]] = []
        for slug in tag_slugs:
            if slug not in seen:
                seen.add(slug)
                rows.append((deck_id, slug))
        if rows:
            await self._conn().executemany(
                "INSERT INTO deck_tags (deck_id, tag_slug) VALUES (?, ?)",
                rows,
            )
        await self._conn().commit()
