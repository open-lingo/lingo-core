"""SQLite-backed SRS repository — FSRS-6 with modality split.

One row per (user, card), keyed by our internal user UUID.
Full FSRS-6 state stored as JSON; due_date column holds
min(recognition.dueDate, production.dueDate) for efficient index queries.
"""

import json
from typing import Any

import aiosqlite

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS srs_cards_v2 (
    user_id    TEXT NOT NULL,
    card_id    TEXT NOT NULL,
    due_date   TEXT NOT NULL,
    state_json TEXT NOT NULL,
    PRIMARY KEY (user_id, card_id)
);

CREATE INDEX IF NOT EXISTS idx_srs_v2_due ON srs_cards_v2 (user_id, due_date);
"""

_DROP_LEGACY = "DROP TABLE IF EXISTS srs_cards;"


def _min_due(state: dict[str, Any]) -> str:
    r = state.get("recognition", {}).get("dueDate", "")
    p = state.get("production", {}).get("dueDate", "")
    if not r:
        return p
    if not p:
        return r
    return min(r, p)


def _max_last_review(state: dict[str, Any]) -> str:
    r = state.get("recognition", {}).get("lastReviewDate", "")
    p = state.get("production", {}).get("lastReviewDate", "")
    return max(r, p)


def _row_to_state(row: aiosqlite.Row) -> dict[str, Any]:
    return json.loads(row["state_json"])


class SqliteSRSRepository:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_DROP_LEGACY + _INIT_SQL)

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    def _conn(self) -> aiosqlite.Connection:
        assert self._db is not None, "call connect() first"
        return self._db

    async def get_all(self, user_id: str) -> dict[str, dict[str, Any]]:
        cur = await self._conn().execute(
            "SELECT card_id, state_json FROM srs_cards_v2 WHERE user_id = ?",
            (user_id,),
        )
        rows = await cur.fetchall()
        return {row["card_id"]: _row_to_state(row) for row in rows}

    async def get_due_cards(
        self, user_id: str, on_or_before: str
    ) -> dict[str, dict[str, Any]]:
        cur = await self._conn().execute(
            """SELECT card_id, state_json FROM srs_cards_v2
               WHERE user_id = ? AND due_date <= ?
               ORDER BY due_date""",
            (user_id, on_or_before),
        )
        rows = await cur.fetchall()
        return {row["card_id"]: _row_to_state(row) for row in rows}

    async def get_card(self, user_id: str, card_id: str) -> dict[str, Any] | None:
        cur = await self._conn().execute(
            "SELECT state_json FROM srs_cards_v2 WHERE user_id = ? AND card_id = ?",
            (user_id, card_id),
        )
        row = await cur.fetchone()
        return _row_to_state(row) if row else None

    async def upsert_cards(
        self, user_id: str, cards: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}

        for card_id, incoming in cards.items():
            existing = await self.get_card(user_id, card_id)

            incoming_review = _max_last_review(incoming)
            existing_review = _max_last_review(existing) if existing else ""
            core_win = existing and existing_review >= incoming_review

            bury_changed = (
                existing
                and "buriedUntil" in incoming
                and incoming.get("buriedUntil") != existing.get("buriedUntil")
            )
            if core_win and not bury_changed:
                result[card_id] = existing
                continue

            if core_win and bury_changed and existing:
                incoming = {**existing, "buriedUntil": incoming.get("buriedUntil")}

            due = _min_due(incoming)
            await self._conn().execute(
                """INSERT INTO srs_cards_v2
                       (user_id, card_id, due_date, state_json)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(user_id, card_id) DO UPDATE SET
                       due_date   = excluded.due_date,
                       state_json = excluded.state_json""",
                (user_id, card_id, due, json.dumps(incoming, ensure_ascii=False)),
            )
            result[card_id] = incoming

        await self._conn().commit()
        return result

    async def delete_cards(self, user_id: str, card_ids: list[str]) -> int:
        if not card_ids:
            return 0
        placeholders = ",".join("?" for _ in card_ids)
        cur = await self._conn().execute(
            f"DELETE FROM srs_cards_v2 WHERE user_id = ? AND card_id IN ({placeholders})",
            [user_id, *card_ids],
        )
        await self._conn().commit()
        return cur.rowcount

    async def clear_all(self, user_id: str) -> int:
        cur = await self._conn().execute(
            "DELETE FROM srs_cards_v2 WHERE user_id = ?", (user_id,)
        )
        await self._conn().commit()
        return cur.rowcount
