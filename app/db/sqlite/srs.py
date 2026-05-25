"""SQLite-backed SRS repository.

One row per (user, card) keyed by our internal user UUID. Card state is
stored as an opaque JSON ``payload`` blob plus three indexed scalar columns
used for queries / LWW merge:

  ``due_date``         — extracted from payload for the due-date index.
  ``last_reviewed_at`` — ISO timestamp used for LWW merge (lex-sortable).
"""

import json
from typing import Any

import aiosqlite

from app.shared.utils import earliest_due_date

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS srs_cards (
    user_id          TEXT NOT NULL,
    card_id          TEXT NOT NULL,
    payload          TEXT NOT NULL DEFAULT '{}',
    due_date         TEXT NOT NULL DEFAULT '',
    last_reviewed_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (user_id, card_id)
);

CREATE INDEX IF NOT EXISTS idx_srs_due ON srs_cards (user_id, due_date);
"""

# Why: legacy installs may have the SM-2 columns; add the new columns
# idempotently so existing local.db files keep working. We do not drop
# old columns — they're left null/empty and ignored.
_MIGRATION_COLS = [
    ("payload", "TEXT NOT NULL DEFAULT '{}'"),
    ("last_reviewed_at", "TEXT NOT NULL DEFAULT ''"),
]


class SqliteSRSRepository:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_INIT_SQL)
        for col, col_type in _MIGRATION_COLS:
            try:
                await self._conn().execute(
                    f"ALTER TABLE srs_cards ADD COLUMN {col} {col_type}"
                )
                await self._conn().commit()
            except aiosqlite.OperationalError as e:
                if "duplicate column name" not in str(e).lower():
                    raise

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    def _conn(self) -> aiosqlite.Connection:
        assert self._db is not None, "call connect() first"
        return self._db

    def _row_to_state(self, row: aiosqlite.Row) -> dict[str, Any]:
        try:
            payload = json.loads(row["payload"] or "{}")
        except (json.JSONDecodeError, TypeError):
            payload = {}
        return payload

    async def get_all(self, user_id: str) -> dict[str, dict[str, Any]]:
        cur = await self._conn().execute(
            "SELECT * FROM srs_cards WHERE user_id = ?", (user_id,)
        )
        rows = await cur.fetchall()
        return {row["card_id"]: self._row_to_state(row) for row in rows}

    async def get_due_cards(
        self, user_id: str, on_or_before: str
    ) -> dict[str, dict[str, Any]]:
        """Return cards with due_date <= on_or_before. Uses idx_srs_due."""
        cur = await self._conn().execute(
            """SELECT * FROM srs_cards
               WHERE user_id = ? AND due_date <= ?
               ORDER BY due_date""",
            (user_id, on_or_before),
        )
        rows = await cur.fetchall()
        return {row["card_id"]: self._row_to_state(row) for row in rows}

    async def get_card(self, user_id: str, card_id: str) -> dict[str, Any] | None:
        cur = await self._conn().execute(
            "SELECT * FROM srs_cards WHERE user_id = ? AND card_id = ?",
            (user_id, card_id),
        )
        row = await cur.fetchone()
        return self._row_to_state(row) if row else None

    async def _get_row(self, user_id: str, card_id: str) -> aiosqlite.Row | None:
        cur = await self._conn().execute(
            "SELECT * FROM srs_cards WHERE user_id = ? AND card_id = ?",
            (user_id, card_id),
        )
        return await cur.fetchone()

    async def upsert_cards(
        self, user_id: str, cards: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}

        for card_id, incoming in cards.items():
            existing_row = await self._get_row(user_id, card_id)
            existing_payload = self._row_to_state(existing_row) if existing_row else None
            existing_last = (
                existing_row["last_reviewed_at"] if existing_row else ""
            ) or ""
            incoming_last = str(incoming.get("lastReviewedAt") or "")

            # Why: ISO-8601 sorts lexicographically — safe string compare.
            server_wins = (
                existing_payload is not None and existing_last >= incoming_last
            )

            bury_changed = (
                existing_payload is not None
                and "buriedUntil" in incoming
                and incoming.get("buriedUntil") != existing_payload.get("buriedUntil")
            )

            if server_wins and not bury_changed:
                result[card_id] = existing_payload  # type: ignore[assignment]
                continue

            if server_wins and bury_changed and existing_payload is not None:
                incoming = {
                    **existing_payload,
                    "buriedUntil": incoming.get("buriedUntil"),
                }

            due = earliest_due_date(incoming)
            await self._conn().execute(
                """INSERT INTO srs_cards
                       (user_id, card_id, payload, due_date, last_reviewed_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, card_id) DO UPDATE SET
                       payload          = excluded.payload,
                       due_date         = excluded.due_date,
                       last_reviewed_at = excluded.last_reviewed_at""",
                (
                    user_id,
                    card_id,
                    json.dumps(incoming),
                    due,
                    incoming_last,
                ),
            )
            result[card_id] = incoming

        await self._conn().commit()
        return result

    async def delete_cards(self, user_id: str, card_ids: list[str]) -> int:
        if not card_ids:
            return 0
        placeholders = ",".join("?" for _ in card_ids)
        cur = await self._conn().execute(
            f"DELETE FROM srs_cards WHERE user_id = ? AND card_id IN ({placeholders})",
            [user_id, *card_ids],
        )
        await self._conn().commit()
        return cur.rowcount

    async def clear_all(self, user_id: str) -> int:
        cur = await self._conn().execute(
            "DELETE FROM srs_cards WHERE user_id = ?", (user_id,)
        )
        await self._conn().commit()
        return cur.rowcount
