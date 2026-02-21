"""SQLite-backed SRS repository.

Mirrors the DynamoDB single-table approach: one row per (user, card).
Indexed columns for due_date enable efficient server-side due-count queries.
"""

import json
from typing import Any

import aiosqlite

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS srs_cards (
    auth0_id        TEXT    NOT NULL,
    card_id         TEXT    NOT NULL,
    ease_factor     REAL    NOT NULL DEFAULT 2.5,
    interval_days   INTEGER NOT NULL DEFAULT 0,
    due_date        TEXT    NOT NULL,
    repetitions     INTEGER NOT NULL DEFAULT 0,
    last_review     TEXT    NOT NULL,
    extra           TEXT    NOT NULL DEFAULT '{}',
    PRIMARY KEY (auth0_id, card_id)
);

CREATE INDEX IF NOT EXISTS idx_srs_due ON srs_cards (auth0_id, due_date);
"""


def _row_to_state(row: aiosqlite.Row) -> dict[str, Any]:
    extra = json.loads(row["extra"]) if row["extra"] else {}
    return {
        "easeFactor": row["ease_factor"],
        "interval": row["interval_days"],
        "dueDate": row["due_date"],
        "repetitions": row["repetitions"],
        "lastReviewDate": row["last_review"],
        **extra,
    }


class SqliteSRSRepository:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
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

    async def get_all(self, auth0_id: str) -> dict[str, dict[str, Any]]:
        cur = await self._conn().execute(
            "SELECT * FROM srs_cards WHERE auth0_id = ?", (auth0_id,)
        )
        rows = await cur.fetchall()
        return {row["card_id"]: _row_to_state(row) for row in rows}

    async def get_due_cards(
        self, auth0_id: str, on_or_before: str
    ) -> dict[str, dict[str, Any]]:
        """Return cards with due_date <= on_or_before. Uses idx_srs_due."""
        cur = await self._conn().execute(
            """SELECT * FROM srs_cards
               WHERE auth0_id = ? AND due_date <= ?
               ORDER BY due_date""",
            (auth0_id, on_or_before),
        )
        rows = await cur.fetchall()
        return {row["card_id"]: _row_to_state(row) for row in rows}

    async def get_card(self, auth0_id: str, card_id: str) -> dict[str, Any] | None:
        cur = await self._conn().execute(
            "SELECT * FROM srs_cards WHERE auth0_id = ? AND card_id = ?",
            (auth0_id, card_id),
        )
        row = await cur.fetchone()
        return _row_to_state(row) if row else None

    async def upsert_cards(
        self, auth0_id: str, cards: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}

        for card_id, incoming in cards.items():
            existing = await self.get_card(auth0_id, card_id)

            # Last-write-wins by lastReviewDate for core SRS fields
            core_win = existing and existing.get("lastReviewDate", "") >= incoming.get("lastReviewDate", "")
            # Always accept buriedUntil changes (bury/unbury) even if core is older
            bury_changed = (
                existing
                and "buriedUntil" in incoming
                and incoming.get("buriedUntil") != existing.get("buriedUntil")
            )
            if core_win and not bury_changed:
                result[card_id] = existing
                continue

            # If only bury changed, merge buriedUntil into existing for persist
            if core_win and bury_changed and existing:
                incoming = {**existing, "buriedUntil": incoming.get("buriedUntil")}

            extra_keys = {k: v for k, v in incoming.items()
                         if k not in ("easeFactor", "interval", "dueDate", "repetitions", "lastReviewDate")}

            await self._conn().execute(
                """INSERT INTO srs_cards
                       (auth0_id, card_id, ease_factor, interval_days, due_date, repetitions, last_review, extra)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(auth0_id, card_id) DO UPDATE SET
                       ease_factor   = excluded.ease_factor,
                       interval_days = excluded.interval_days,
                       due_date      = excluded.due_date,
                       repetitions   = excluded.repetitions,
                       last_review   = excluded.last_review,
                       extra         = excluded.extra""",
                (
                    auth0_id,
                    card_id,
                    incoming.get("easeFactor", 2.5),
                    incoming.get("interval", 0),
                    incoming.get("dueDate", ""),
                    incoming.get("repetitions", 0),
                    incoming.get("lastReviewDate", ""),
                    json.dumps(extra_keys) if extra_keys else "{}",
                ),
            )
            result[card_id] = incoming

        await self._conn().commit()
        return result

    async def delete_cards(self, auth0_id: str, card_ids: list[str]) -> int:
        if not card_ids:
            return 0
        placeholders = ",".join("?" for _ in card_ids)
        cur = await self._conn().execute(
            f"DELETE FROM srs_cards WHERE auth0_id = ? AND card_id IN ({placeholders})",
            [auth0_id, *card_ids],
        )
        await self._conn().commit()
        return cur.rowcount

    async def clear_all(self, auth0_id: str) -> int:
        cur = await self._conn().execute(
            "DELETE FROM srs_cards WHERE auth0_id = ?", (auth0_id,)
        )
        await self._conn().commit()
        return cur.rowcount
