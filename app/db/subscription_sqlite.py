"""SQLite-backed subscription repository.

Separate table from user_settings — different query pattern.
DynamoDB migration: PK=USER#auth0_id, SK=SUB#type#content_id.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS subscriptions (
    auth0_id           TEXT NOT NULL,
    content_type       TEXT NOT NULL,
    content_id         TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    enabled            INTEGER NOT NULL DEFAULT 1,
    new_cards_per_day  INTEGER NOT NULL DEFAULT 5,
    new_card_order     TEXT NOT NULL DEFAULT 'ordered',
    PRIMARY KEY (auth0_id, content_type, content_id)
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_auth0 ON subscriptions (auth0_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_auth0_type ON subscriptions (auth0_id, content_type);
"""


def _row_to_item(row: aiosqlite.Row) -> dict[str, Any]:
    return {
        "contentType": row["content_type"],
        "contentId": row["content_id"],
        "createdAt": row["created_at"],
        "enabled": bool(row["enabled"]) if "enabled" in row.keys() else True,
        "newCardsPerDay": int(row["new_cards_per_day"]) if "new_cards_per_day" in row.keys() else 5,
        "newCardOrder": str(row["new_card_order"]) if "new_card_order" in row.keys() else "ordered",
    }


class SqliteSubscriptionRepository:
    def __init__(self, db_path: str) -> None:
        self._db_path = str(Path(db_path).resolve())
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_INIT_SQL)
        # Migration: add subscription settings columns
        for col, col_def, default in [
            ("enabled", "INTEGER NOT NULL DEFAULT 1", 1),
            ("new_cards_per_day", "INTEGER NOT NULL DEFAULT 5", 5),
            ("new_card_order", "TEXT NOT NULL DEFAULT 'ordered'", "ordered"),
        ]:
            try:
                await self._db.execute(
                    f"ALTER TABLE subscriptions ADD COLUMN {col} {col_def}"
                )
                await self._db.commit()
            except Exception as e:
                if "duplicate column name" not in str(e).lower():
                    raise

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    def _conn(self) -> aiosqlite.Connection:
        assert self._db is not None, "call connect() first"
        return self._db

    async def add(self, auth0_id: str, content_type: str, content_id: str) -> None:
        now = datetime.now(UTC).isoformat()
        await self._conn().execute(
            """INSERT INTO subscriptions (auth0_id, content_type, content_id, created_at, enabled, new_cards_per_day, new_card_order)
               VALUES (?, ?, ?, ?, 1, 5, 'ordered')
               ON CONFLICT(auth0_id, content_type, content_id) DO UPDATE SET created_at = excluded.created_at""",
            (auth0_id, content_type, content_id, now),
        )
        await self._conn().commit()

    async def update_settings(
        self,
        auth0_id: str,
        content_type: str,
        content_id: str,
        patch: dict[str, Any],
    ) -> bool:
        """Update subscription settings. Returns True if updated, False if not found."""
        if not patch:
            return False
        sets = []
        params: list[Any] = []
        if "enabled" in patch:
            sets.append("enabled = ?")
            params.append(1 if patch["enabled"] else 0)
        if "newCardsPerDay" in patch:
            sets.append("new_cards_per_day = ?")
            params.append(patch["newCardsPerDay"])
        if "newCardOrder" in patch:
            sets.append("new_card_order = ?")
            params.append(patch["newCardOrder"])
        if not sets:
            return False
        params.extend([auth0_id, content_type, content_id])
        cur = await self._conn().execute(
            f"""UPDATE subscriptions SET {", ".join(sets)}
                WHERE auth0_id = ? AND content_type = ? AND content_id = ?""",
            params,
        )
        await self._conn().commit()
        return cur.rowcount > 0

    async def remove(self, auth0_id: str, content_type: str, content_id: str) -> None:
        await self._conn().execute(
            """DELETE FROM subscriptions WHERE auth0_id = ? AND content_type = ? AND content_id = ?""",
            (auth0_id, content_type, content_id),
        )
        await self._conn().commit()

    async def list(
        self, auth0_id: str, content_type: str | None = None
    ) -> list[dict[str, Any]]:
        if content_type:
            cur = await self._conn().execute(
                """SELECT * FROM subscriptions
                   WHERE auth0_id = ? AND content_type = ?
                   ORDER BY content_type, content_id""",
                (auth0_id, content_type),
            )
        else:
            cur = await self._conn().execute(
                """SELECT * FROM subscriptions
                   WHERE auth0_id = ?
                   ORDER BY content_type, content_id""",
                (auth0_id,),
            )
        rows = await cur.fetchall()
        return [_row_to_item(row) for row in rows]

    async def has(self, auth0_id: str, content_type: str, content_id: str) -> bool:
        cur = await self._conn().execute(
            """SELECT 1 FROM subscriptions WHERE auth0_id = ? AND content_type = ? AND content_id = ?""",
            (auth0_id, content_type, content_id),
        )
        return await cur.fetchone() is not None
