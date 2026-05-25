"""SQLite-backed social repository (local development).

Two tables:

  ``social`` — friend graph + blocks. One row per (owner_id, kind, other_id).
    kind ∈ {FRIEND, REQUEST_IN, REQUEST_OUT, BLOCK}
    Mirrored writes ensure both "sides" of a relationship can be queried by
    owner with a single index hit.

  ``social_leaderboard`` — XP per (bucket, user_id). bucket = "<lang>#<period>"
    where period is ISO week ("2026-W21") or month ("2026-05").

See ``app/db/protocols/social.py`` for the full contract.
"""

import json
from datetime import UTC, datetime
from typing import Any

import aiosqlite

_KIND_FRIEND = "FRIEND"
_KIND_REQUEST_IN = "REQUEST_IN"
_KIND_REQUEST_OUT = "REQUEST_OUT"
_KIND_BLOCK = "BLOCK"

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS social (
    owner_id   TEXT NOT NULL,
    kind       TEXT NOT NULL,
    other_id   TEXT NOT NULL,
    created_at TEXT NOT NULL,
    metadata   TEXT,
    PRIMARY KEY (owner_id, kind, other_id)
);

CREATE INDEX IF NOT EXISTS idx_social_other_id ON social(other_id);
CREATE INDEX IF NOT EXISTS idx_social_owner_kind ON social(owner_id, kind);

CREATE TABLE IF NOT EXISTS social_leaderboard (
    bucket       TEXT NOT NULL,
    user_id      TEXT NOT NULL,
    xp           INTEGER NOT NULL DEFAULT 0,
    lessons      INTEGER NOT NULL DEFAULT 0,
    last_updated TEXT NOT NULL,
    PRIMARY KEY (bucket, user_id)
);

CREATE INDEX IF NOT EXISTS idx_leaderboard_bucket_xp
    ON social_leaderboard(bucket, xp DESC);
"""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
    md = row["metadata"]
    return {
        "owner_id": row["owner_id"],
        "kind": row["kind"],
        "other_id": row["other_id"],
        "created_at": row["created_at"],
        "metadata": json.loads(md) if md else None,
    }


def _leaderboard_row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
    return {
        "user_id": row["user_id"],
        "xp": int(row["xp"]),
        "lessons": int(row["lessons"]),
        "last_updated": row["last_updated"],
    }


class SqliteSocialRepository:
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

    # ── Friend graph ────────────────────────────────────────────────────────

    async def list_friends(self, user_id: str) -> list[dict[str, Any]]:
        cur = await self._conn().execute(
            "SELECT * FROM social WHERE owner_id = ? AND kind = ? ORDER BY created_at DESC",
            (user_id, _KIND_FRIEND),
        )
        rows = await cur.fetchall()
        return [_row_to_dict(r) for r in rows]

    async def list_friend_requests(
        self, user_id: str
    ) -> dict[str, list[dict[str, Any]]]:
        cur = await self._conn().execute(
            "SELECT * FROM social WHERE owner_id = ? AND kind = ? ORDER BY created_at DESC",
            (user_id, _KIND_REQUEST_IN),
        )
        incoming_rows = await cur.fetchall()
        cur = await self._conn().execute(
            "SELECT * FROM social WHERE owner_id = ? AND kind = ? ORDER BY created_at DESC",
            (user_id, _KIND_REQUEST_OUT),
        )
        outgoing_rows = await cur.fetchall()
        return {
            "incoming": [_row_to_dict(r) for r in incoming_rows],
            "outgoing": [_row_to_dict(r) for r in outgoing_rows],
        }

    async def get_relationship(
        self, owner_id: str, other_id: str
    ) -> dict[str, Any] | None:
        cur = await self._conn().execute(
            "SELECT * FROM social WHERE owner_id = ? AND other_id = ? LIMIT 1",
            (owner_id, other_id),
        )
        row = await cur.fetchone()
        return _row_to_dict(row) if row else None

    async def _get_relationship_of_kind(
        self, owner_id: str, kind: str, other_id: str
    ) -> dict[str, Any] | None:
        cur = await self._conn().execute(
            "SELECT * FROM social WHERE owner_id = ? AND kind = ? AND other_id = ?",
            (owner_id, kind, other_id),
        )
        row = await cur.fetchone()
        return _row_to_dict(row) if row else None

    async def send_friend_request(
        self, from_user_id: str, to_user_id: str
    ) -> None:
        now = _now_iso()
        conn = self._conn()
        # SQLite via aiosqlite is auto-commit per execute by default; use a
        # BEGIN/COMMIT block so the mirrored pair lands atomically.
        await conn.execute("BEGIN")
        try:
            await conn.execute(
                """INSERT OR IGNORE INTO social
                   (owner_id, kind, other_id, created_at, metadata)
                   VALUES (?, ?, ?, ?, NULL)""",
                (from_user_id, _KIND_REQUEST_OUT, to_user_id, now),
            )
            await conn.execute(
                """INSERT OR IGNORE INTO social
                   (owner_id, kind, other_id, created_at, metadata)
                   VALUES (?, ?, ?, ?, NULL)""",
                (to_user_id, _KIND_REQUEST_IN, from_user_id, now),
            )
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise

    async def accept_friend_request(
        self, accepter_id: str, requester_id: str
    ) -> bool:
        # Verify there's a pending REQUEST_IN row from requester→accepter.
        existing = await self._get_relationship_of_kind(
            accepter_id, _KIND_REQUEST_IN, requester_id
        )
        if existing is None:
            return False

        now = _now_iso()
        conn = self._conn()
        await conn.execute("BEGIN")
        try:
            await conn.execute(
                "DELETE FROM social WHERE owner_id = ? AND kind = ? AND other_id = ?",
                (accepter_id, _KIND_REQUEST_IN, requester_id),
            )
            await conn.execute(
                "DELETE FROM social WHERE owner_id = ? AND kind = ? AND other_id = ?",
                (requester_id, _KIND_REQUEST_OUT, accepter_id),
            )
            await conn.execute(
                """INSERT OR IGNORE INTO social
                   (owner_id, kind, other_id, created_at, metadata)
                   VALUES (?, ?, ?, ?, NULL)""",
                (accepter_id, _KIND_FRIEND, requester_id, now),
            )
            await conn.execute(
                """INSERT OR IGNORE INTO social
                   (owner_id, kind, other_id, created_at, metadata)
                   VALUES (?, ?, ?, ?, NULL)""",
                (requester_id, _KIND_FRIEND, accepter_id, now),
            )
            await conn.commit()
            return True
        except Exception:
            await conn.rollback()
            raise

    async def delete_friend_request(
        self, owner_id: str, other_id: str
    ) -> bool:
        conn = self._conn()
        await conn.execute("BEGIN")
        try:
            cur = await conn.execute(
                """DELETE FROM social
                   WHERE (owner_id = ? AND kind IN (?, ?) AND other_id = ?)
                      OR (owner_id = ? AND kind IN (?, ?) AND other_id = ?)""",
                (
                    owner_id, _KIND_REQUEST_IN, _KIND_REQUEST_OUT, other_id,
                    other_id, _KIND_REQUEST_IN, _KIND_REQUEST_OUT, owner_id,
                ),
            )
            await conn.commit()
            return cur.rowcount > 0
        except Exception:
            await conn.rollback()
            raise

    async def unfriend(self, user_id: str, friend_id: str) -> bool:
        conn = self._conn()
        await conn.execute("BEGIN")
        try:
            cur = await conn.execute(
                """DELETE FROM social
                   WHERE kind = ?
                     AND ((owner_id = ? AND other_id = ?)
                          OR (owner_id = ? AND other_id = ?))""",
                (_KIND_FRIEND, user_id, friend_id, friend_id, user_id),
            )
            await conn.commit()
            return cur.rowcount > 0
        except Exception:
            await conn.rollback()
            raise

    # ── Blocks ──────────────────────────────────────────────────────────────

    async def block_user(self, owner_id: str, other_id: str) -> None:
        now = _now_iso()
        conn = self._conn()
        await conn.execute("BEGIN")
        try:
            # Insert the BLOCK row (one-directional).
            await conn.execute(
                """INSERT OR IGNORE INTO social
                   (owner_id, kind, other_id, created_at, metadata)
                   VALUES (?, ?, ?, ?, NULL)""",
                (owner_id, _KIND_BLOCK, other_id, now),
            )
            # Cascade: drop any FRIEND / REQUEST rows in either direction.
            await conn.execute(
                """DELETE FROM social
                   WHERE kind IN (?, ?, ?)
                     AND ((owner_id = ? AND other_id = ?)
                          OR (owner_id = ? AND other_id = ?))""",
                (
                    _KIND_FRIEND, _KIND_REQUEST_IN, _KIND_REQUEST_OUT,
                    owner_id, other_id,
                    other_id, owner_id,
                ),
            )
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise

    async def unblock_user(self, owner_id: str, other_id: str) -> bool:
        cur = await self._conn().execute(
            "DELETE FROM social WHERE owner_id = ? AND kind = ? AND other_id = ?",
            (owner_id, _KIND_BLOCK, other_id),
        )
        await self._conn().commit()
        return cur.rowcount > 0

    async def list_blocks(self, owner_id: str) -> list[dict[str, Any]]:
        cur = await self._conn().execute(
            "SELECT * FROM social WHERE owner_id = ? AND kind = ? ORDER BY created_at DESC",
            (owner_id, _KIND_BLOCK),
        )
        rows = await cur.fetchall()
        return [_row_to_dict(r) for r in rows]

    async def is_blocked(self, owner_id: str, other_id: str) -> bool:
        cur = await self._conn().execute(
            "SELECT 1 FROM social WHERE owner_id = ? AND kind = ? AND other_id = ?",
            (owner_id, _KIND_BLOCK, other_id),
        )
        row = await cur.fetchone()
        return row is not None

    # ── Leaderboards ────────────────────────────────────────────────────────

    async def add_xp_to_leaderboard(
        self, user_id: str, lang: str, xp_delta: int
    ) -> None:
        if xp_delta <= 0:
            return
        now = datetime.now(UTC)
        # ISO week. (year, week, weekday); use the ISO calendar year so
        # late-December weeks belong to the right year.
        iso = now.isocalendar()
        week_bucket = f"{lang}#{iso.year:04d}-W{iso.week:02d}"
        month_bucket = f"{lang}#{now.year:04d}-{now.month:02d}"
        ts = now.isoformat()
        conn = self._conn()
        for bucket in (week_bucket, month_bucket):
            await conn.execute(
                """
                INSERT INTO social_leaderboard
                  (bucket, user_id, xp, lessons, last_updated)
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(bucket, user_id) DO UPDATE SET
                    xp = xp + excluded.xp,
                    lessons = lessons + 1,
                    last_updated = excluded.last_updated
                """,
                (bucket, user_id, int(xp_delta), ts),
            )
        await conn.commit()

    async def get_leaderboard(
        self,
        bucket: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        cur = await self._conn().execute(
            """SELECT * FROM social_leaderboard
               WHERE bucket = ?
               ORDER BY xp DESC, user_id ASC
               LIMIT ? OFFSET ?""",
            (bucket, limit, offset),
        )
        rows = await cur.fetchall()
        return [_leaderboard_row_to_dict(r) for r in rows]

    async def get_user_leaderboard_entry(
        self, bucket: str, user_id: str
    ) -> dict[str, Any] | None:
        cur = await self._conn().execute(
            "SELECT * FROM social_leaderboard WHERE bucket = ? AND user_id = ?",
            (bucket, user_id),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        entry = _leaderboard_row_to_dict(row)
        # Compute rank: count of rows with strictly greater xp + 1.
        cur = await self._conn().execute(
            "SELECT COUNT(*) AS c FROM social_leaderboard WHERE bucket = ? AND xp > ?",
            (bucket, entry["xp"]),
        )
        higher_row = await cur.fetchone()
        higher = int(higher_row["c"]) if higher_row else 0
        cur = await self._conn().execute(
            "SELECT COUNT(*) AS c FROM social_leaderboard WHERE bucket = ?",
            (bucket,),
        )
        total_row = await cur.fetchone()
        total = int(total_row["c"]) if total_row else 0
        entry["rank"] = higher + 1
        entry["total"] = total
        return entry

    async def get_friends_leaderboard(
        self, user_id: str, bucket: str
    ) -> list[dict[str, Any]]:
        cur = await self._conn().execute(
            "SELECT other_id FROM social WHERE owner_id = ? AND kind = ?",
            (user_id, _KIND_FRIEND),
        )
        friend_rows = await cur.fetchall()
        ids = [user_id] + [r["other_id"] for r in friend_rows]
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        cur = await self._conn().execute(
            f"""SELECT * FROM social_leaderboard
                WHERE bucket = ? AND user_id IN ({placeholders})
                ORDER BY xp DESC, user_id ASC""",
            (bucket, *ids),
        )
        rows = await cur.fetchall()
        return [_leaderboard_row_to_dict(r) for r in rows]
