"""SQLite-backed story repository."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS stories (
    id                TEXT PRIMARY KEY,
    language_id       TEXT NOT NULL,
    title             TEXT NOT NULL,
    description       TEXT,
    companion_deck_id TEXT NOT NULL,
    body              TEXT NOT NULL DEFAULT '',
    author_id         TEXT,
    status            TEXT NOT NULL DEFAULT 'draft',
    created_at        TEXT,
    updated_at        TEXT
);

CREATE INDEX IF NOT EXISTS idx_stories_author ON stories (author_id) WHERE author_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_stories_language ON stories (language_id);
"""


def _row_to_story(row: aiosqlite.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "languageId": row["language_id"],
        "title": row["title"],
        "description": row["description"],
        "companionDeckId": row["companion_deck_id"],
        "body": row["body"],
        "authorId": row["author_id"],
        "status": row["status"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


class SqliteStoryRepository:
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
        assert self._db is not None
        return self._db

    async def list_stories(
        self,
        author_id: str | None = None,
        language_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        conditions = []
        params: list[Any] = []
        if author_id:
            conditions.append("author_id = ?")
            params.append(author_id)
        if language_id:
            conditions.append("language_id = ?")
            params.append(language_id)
        if status:
            conditions.append("status = ?")
            params.append(status)
        where = " AND ".join(conditions) if conditions else "1=1"
        cur = await self._conn().execute(
            f"SELECT * FROM stories WHERE {where} ORDER BY updated_at DESC, title",
            params,
        )
        rows = await cur.fetchall()
        return [_row_to_story(row) for row in rows]

    async def get_story(self, story_id: str) -> dict[str, Any] | None:
        cur = await self._conn().execute(
            "SELECT * FROM stories WHERE id = ?",
            (story_id,),
        )
        row = await cur.fetchone()
        return _row_to_story(row) if row else None

    async def create_story(
        self,
        story_id: str,
        data: dict[str, Any],
    ) -> None:
        now = datetime.now(UTC).isoformat()
        await self._conn().execute(
            """INSERT INTO stories
                   (id, language_id, title, description, companion_deck_id, body, author_id, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                story_id,
                data.get("languageId", ""),
                data.get("title", ""),
                data.get("description"),
                data.get("companionDeckId", ""),
                data.get("body", ""),
                data.get("authorId"),
                data.get("status", "draft"),
                now,
                now,
            ),
        )
        await self._conn().commit()

    async def delete_story(self, story_id: str) -> None:
        await self._conn().execute("DELETE FROM stories WHERE id = ?", (story_id,))
        await self._conn().commit()

    async def update_story(
        self,
        story_id: str,
        data: dict[str, Any],
    ) -> None:
        existing = await self.get_story(story_id)
        if not existing:
            return
        merged = dict(existing)
        for k, v in data.items():
            if v is not None or k in ("description", "body"):
                merged[k] = v
        now = datetime.now(UTC).isoformat()
        await self._conn().execute(
            """UPDATE stories SET
                   language_id = ?,
                   title = ?,
                   description = ?,
                   companion_deck_id = ?,
                   body = ?,
                   status = ?,
                   updated_at = ?
               WHERE id = ?""",
            (
                merged.get("languageId", ""),
                merged.get("title", ""),
                merged.get("description"),
                merged.get("companionDeckId", ""),
                merged.get("body", ""),
                merged.get("status", "draft"),
                now,
                story_id,
            ),
        )
        await self._conn().commit()
