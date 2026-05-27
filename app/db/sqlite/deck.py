"""SQLite-backed deck repository.

Deck manifest (metadata) and deck content (cards) stored separately.
Both keyed by deck id. Matches design in docs/dataformats/flashcards/deck-manifest.md.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS deck_manifests (
    id                   TEXT PRIMARY KEY,
    language_id          TEXT NOT NULL,
    name                 TEXT NOT NULL,
    description          TEXT,
    course_id            TEXT,
    author_id            TEXT,
    status               TEXT NOT NULL DEFAULT 'draft',
    version              TEXT NOT NULL DEFAULT '1.0',
    card_count           INTEGER NOT NULL DEFAULT 0,
    companion_to_story_id TEXT,
    created_at           TEXT,
    updated_at           TEXT
);

CREATE INDEX IF NOT EXISTS idx_deck_manifests_language ON deck_manifests (language_id);
CREATE INDEX IF NOT EXISTS idx_deck_manifests_course ON deck_manifests (course_id) WHERE course_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS deck_content (
    deck_id TEXT PRIMARY KEY,
    cards   TEXT NOT NULL,
    FOREIGN KEY (deck_id) REFERENCES deck_manifests (id)
);

-- Per-user upvotes. PRIMARY KEY (deck_id, user_id) makes voting idempotent
-- naturally — INSERT OR IGNORE collapses a second vote into a no-op.
CREATE TABLE IF NOT EXISTS deck_votes (
    deck_id    TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (deck_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_deck_votes_deck ON deck_votes (deck_id);
"""


def _row_to_manifest(row: aiosqlite.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "languageId": row["language_id"],
        "name": row["name"],
        "description": row["description"] if "description" in row.keys() else None,
        "courseId": row["course_id"],
        "authorId": row["author_id"] if "author_id" in row.keys() else None,
        "status": row["status"] if "status" in row.keys() else "published",
        "version": row["version"],
        "cardCount": row["card_count"],
        "image": row["image"] if "image" in row.keys() else None,
        "defaultEase": row["default_ease"] if "default_ease" in row.keys() else None,
        "locale": row["locale"] if "locale" in row.keys() else None,
        "companionToStoryId": row["companion_to_story_id"] if "companion_to_story_id" in row.keys() else None,
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


class SqliteDeckRepository:
    def __init__(self, db_path: str) -> None:
        self._db_path = str(Path(db_path).resolve())
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_INIT_SQL)
        # Migration: add image column if missing (existing DBs)
        try:
            await self._db.execute("ALTER TABLE deck_manifests ADD COLUMN image TEXT")
            await self._db.commit()
        except Exception as e:
            if "duplicate column name" not in str(e).lower():
                raise
        try:
            await self._db.execute("ALTER TABLE deck_manifests ADD COLUMN locale TEXT")
            await self._db.commit()
        except Exception as e:
            if "duplicate column name" not in str(e).lower():
                raise
        try:
            await self._db.execute("ALTER TABLE deck_manifests ADD COLUMN default_ease REAL")
            await self._db.commit()
        except Exception as e:
            if "duplicate column name" not in str(e).lower():
                raise
        try:
            await self._db.execute("ALTER TABLE deck_manifests ADD COLUMN companion_to_story_id TEXT")
            await self._db.commit()
        except Exception as e:
            if "duplicate column name" not in str(e).lower():
                raise
        # Community deck fields (status=published for existing course decks)
        for col, col_def in [
            ("description", "TEXT"),
            ("author_id", "TEXT"),
            ("status", "TEXT DEFAULT 'published'"),
        ]:
            try:
                await self._db.execute(f"ALTER TABLE deck_manifests ADD COLUMN {col} {col_def}")
                await self._db.commit()
            except Exception as e:
                if "duplicate column name" not in str(e).lower():
                    raise
        # Create indexes after columns exist (for existing DBs that were migrated)
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS idx_deck_manifests_author ON deck_manifests (author_id) WHERE author_id IS NOT NULL",
            "CREATE INDEX IF NOT EXISTS idx_deck_manifests_status ON deck_manifests (status)",
        ]:
            try:
                await self._db.execute(idx_sql)
                await self._db.commit()
            except Exception as e:
                if "duplicate index name" not in str(e).lower() and "already exists" not in str(e).lower():
                    raise

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    def _conn(self) -> aiosqlite.Connection:
        assert self._db is not None, "call connect() first"
        return self._db

    async def list_manifests(
        self,
        language_id: str | None = None,
        author_id: str | None = None,
        status: str | None = None,
        exclude_companion: bool = False,
    ) -> list[dict[str, Any]]:
        conditions = []
        params: list[Any] = []
        if language_id:
            conditions.append("language_id = ?")
            params.append(language_id)
        if author_id:
            conditions.append("author_id = ?")
            params.append(author_id)
        if status:
            conditions.append("status = ?")
            params.append(status)
        if exclude_companion:
            conditions.append("companion_to_story_id IS NULL")
        where = (" AND ".join(conditions)) if conditions else "1=1"
        cur = await self._conn().execute(
            f"SELECT * FROM deck_manifests WHERE {where} ORDER BY updated_at DESC, name",
            params,
        )
        rows = await cur.fetchall()
        return [_row_to_manifest(row) for row in rows]

    async def list_owned_manifests(
        self,
        author_id: str,
        *,
        language_id: str | None = None,
        status: str | None = None,
        exclude_companion: bool = False,
    ) -> list[dict[str, Any]]:
        """Decks authored by this user (My Content / editor). Indexed by ``author_id``."""
        return await self.list_manifests(
            language_id=language_id,
            author_id=author_id,
            status=status,
            exclude_companion=exclude_companion,
        )

    async def get_manifest(self, deck_id: str) -> dict[str, Any] | None:
        cur = await self._conn().execute("SELECT * FROM deck_manifests WHERE id = ?", (deck_id,))
        row = await cur.fetchone()
        return _row_to_manifest(row) if row else None

    async def get_deck(self, deck_id: str) -> dict[str, Any] | None:
        manifest = await self.get_manifest(deck_id)
        if not manifest:
            return None

        cur = await self._conn().execute("SELECT cards FROM deck_content WHERE deck_id = ?", (deck_id,))
        row = await cur.fetchone()
        if not row:
            return None

        cards = json.loads(row["cards"])
        return {**manifest, "cards": cards}

    async def get_decks_batch(self, deck_ids: list[str]) -> list[dict[str, Any]]:
        if not deck_ids:
            return []
        placeholders = ",".join("?" for _ in deck_ids)
        cur = await self._conn().execute(
            f"SELECT * FROM deck_manifests WHERE id IN ({placeholders})",
            deck_ids,
        )
        manifests = {row["id"]: _row_to_manifest(row) for row in await cur.fetchall()}
        cur = await self._conn().execute(
            f"SELECT deck_id, cards FROM deck_content WHERE deck_id IN ({placeholders})",
            deck_ids,
        )
        content = {row["deck_id"]: json.loads(row["cards"]) for row in await cur.fetchall()}
        result = []
        for did in deck_ids:
            manifest = manifests.get(did)
            cards = content.get(did, [])
            if manifest:
                result.append({**manifest, "cards": cards})
        return result

    async def get_versions(self, deck_ids: list[str]) -> dict[str, str]:
        if not deck_ids:
            return {}
        placeholders = ",".join("?" for _ in deck_ids)
        cur = await self._conn().execute(
            f"SELECT id, version FROM deck_manifests WHERE id IN ({placeholders})",
            deck_ids,
        )
        rows = await cur.fetchall()
        return {row["id"]: row["version"] for row in rows}

    async def upsert_deck(self, deck_id: str, manifest: dict[str, Any], cards: list[dict[str, Any]]) -> None:
        now = datetime.now(UTC).isoformat()
        card_count = len(cards)

        companion_to_story_id = manifest.get("companionToStoryId")

        await self._conn().execute(
            """INSERT INTO deck_manifests
                   (id, language_id, name, description, course_id, author_id, status, version, card_count, image, default_ease, locale, companion_to_story_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   language_id = excluded.language_id,
                   name        = excluded.name,
                   description = excluded.description,
                   course_id   = excluded.course_id,
                   author_id   = COALESCE(excluded.author_id, deck_manifests.author_id),
                   status      = excluded.status,
                   version     = excluded.version,
                   card_count  = excluded.card_count,
                   image       = excluded.image,
                   default_ease = excluded.default_ease,
                   locale      = excluded.locale,
                   companion_to_story_id = excluded.companion_to_story_id,
                   updated_at  = excluded.updated_at""",
            (
                deck_id,
                manifest.get("languageId", ""),
                manifest.get("name", ""),
                manifest.get("description"),
                manifest.get("courseId"),
                manifest.get("authorId"),
                manifest.get("status", "draft"),
                manifest.get("version", "1.0"),
                card_count,
                manifest.get("image"),
                manifest.get("defaultEase"),
                manifest.get("locale"),
                companion_to_story_id,
                now,
                now,
            ),
        )

        cards_json = json.dumps(cards)
        await self._conn().execute(
            """INSERT INTO deck_content (deck_id, cards) VALUES (?, ?)
               ON CONFLICT(deck_id) DO UPDATE SET cards = excluded.cards""",
            (deck_id, cards_json),
        )
        await self._conn().commit()

    async def delete_deck(self, deck_id: str) -> None:
        await self._conn().execute("DELETE FROM deck_content WHERE deck_id = ?", (deck_id,))
        await self._conn().execute("DELETE FROM deck_manifests WHERE id = ?", (deck_id,))
        await self._conn().execute("DELETE FROM deck_votes WHERE deck_id = ?", (deck_id,))
        await self._conn().commit()

    # ── Voting ────────────────────────────────────────────────────────────

    async def add_vote(self, deck_id: str, user_id: str) -> None:
        """Idempotent upvote. INSERT OR IGNORE collapses a repeat into a no-op."""
        now = datetime.now(UTC).isoformat()
        await self._conn().execute(
            """INSERT OR IGNORE INTO deck_votes (deck_id, user_id, created_at)
               VALUES (?, ?, ?)""",
            (deck_id, user_id, now),
        )
        await self._conn().commit()

    async def remove_vote(self, deck_id: str, user_id: str) -> None:
        await self._conn().execute(
            "DELETE FROM deck_votes WHERE deck_id = ? AND user_id = ?",
            (deck_id, user_id),
        )
        await self._conn().commit()

    async def get_vote_count(self, deck_id: str) -> int:
        cur = await self._conn().execute(
            "SELECT COUNT(*) AS c FROM deck_votes WHERE deck_id = ?",
            (deck_id,),
        )
        row = await cur.fetchone()
        return int(row["c"]) if row else 0

    async def get_vote_state(self, deck_id: str, user_id: str | None) -> dict[str, Any]:
        count = await self.get_vote_count(deck_id)
        if user_id is None:
            return {"count": count, "voted": False}
        cur = await self._conn().execute(
            "SELECT 1 FROM deck_votes WHERE deck_id = ? AND user_id = ? LIMIT 1",
            (deck_id, user_id),
        )
        row = await cur.fetchone()
        return {"count": count, "voted": row is not None}

    async def get_vote_counts(self, deck_ids: list[str]) -> dict[str, int]:
        if not deck_ids:
            return {}
        placeholders = ",".join("?" for _ in deck_ids)
        cur = await self._conn().execute(
            f"""SELECT deck_id, COUNT(*) AS c
                FROM deck_votes
                WHERE deck_id IN ({placeholders})
                GROUP BY deck_id""",
            deck_ids,
        )
        rows = await cur.fetchall()
        counts = {row["deck_id"]: int(row["c"]) for row in rows}
        # Fill in zeros for missing decks
        return {did: counts.get(did, 0) for did in deck_ids}
