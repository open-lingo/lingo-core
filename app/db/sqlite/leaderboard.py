"""SQLite-backed leaderboard read repository (local development).

Mirrors the DynamoDB ``lingo_social_leaderboard`` shape: one row per
(bucket, user) holding a cumulative ``xp`` for that period. In local dev the
async worker (which owns the writes in prod) isn't running, so this table is
typically empty and the global boards come back empty — acceptable per the
cost-optimization plan ("leaderboard ranking isn't a dev concern"). The table
exists so the read path is identical across backends and so tests/seed can
populate it directly via ``record_xp`` if they want non-empty dev boards.
"""

from typing import Any

import aiosqlite

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS social_leaderboard (
    bucket   TEXT NOT NULL,
    user_id  TEXT NOT NULL,
    xp       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (bucket, user_id)
);
CREATE INDEX IF NOT EXISTS idx_leaderboard_bucket_xp
    ON social_leaderboard (bucket, xp DESC);
"""


class SqliteLeaderboardRepository:
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

    async def top_n(self, bucket: str, limit: int) -> list[dict[str, Any]]:
        cur = await self._conn().execute(
            "SELECT user_id, xp FROM social_leaderboard WHERE bucket = ? "
            "ORDER BY xp DESC, user_id ASC LIMIT ?",
            (bucket, limit),
        )
        rows = await cur.fetchall()
        return [{"user_id": r["user_id"], "xp": int(r["xp"])} for r in rows]

    async def get_entry(self, bucket: str, user_id: str) -> dict[str, Any] | None:
        cur = await self._conn().execute(
            "SELECT user_id, xp FROM social_leaderboard WHERE bucket = ? AND user_id = ?",
            (bucket, user_id),
        )
        row = await cur.fetchone()
        return {"user_id": row["user_id"], "xp": int(row["xp"])} if row else None

    async def rank_for_xp(self, bucket: str, xp: int) -> int:
        cur = await self._conn().execute(
            "SELECT COUNT(*) AS n FROM social_leaderboard WHERE bucket = ? AND xp > ?",
            (bucket, xp),
        )
        row = await cur.fetchone()
        return (int(row["n"]) if row else 0) + 1

    async def bucket_size(self, bucket: str) -> int:
        cur = await self._conn().execute(
            "SELECT COUNT(*) AS n FROM social_leaderboard WHERE bucket = ?",
            (bucket,),
        )
        row = await cur.fetchone()
        return int(row["n"]) if row else 0

    # Dev/test convenience — not on the protocol (prod writes come from
    # lingo-async). Lets a seed script or a test stage non-empty boards.
    async def record_xp(self, bucket: str, user_id: str, xp_inc: int) -> None:
        await self._conn().execute(
            "INSERT INTO social_leaderboard (bucket, user_id, xp) VALUES (?, ?, ?) "
            "ON CONFLICT(bucket, user_id) DO UPDATE SET xp = xp + excluded.xp",
            (bucket, user_id, xp_inc),
        )
        await self._conn().commit()
