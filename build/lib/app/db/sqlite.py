"""SQLite-backed repository for local development.

Stores user data as JSON blobs — mirrors DynamoDB's flexible schema so the
same access patterns work in both backends.  The DB file persists across
server restarts (default: ``local.db`` in the project root).
"""

import json
from typing import Any

import aiosqlite

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS user_settings (
    user_id TEXT PRIMARY KEY,
    data    TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS user_profiles (
    user_id TEXT PRIMARY KEY,
    data    TEXT NOT NULL DEFAULT '{}'
);
"""


class SqliteUserRepository:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.executescript(_INIT_SQL)

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    def _conn(self) -> aiosqlite.Connection:
        assert self._db is not None, "call connect() first"
        return self._db

    # -- UserRepository protocol --

    async def get_settings(self, user_id: str) -> dict[str, Any] | None:
        cur = await self._conn().execute(
            "SELECT data FROM user_settings WHERE user_id = ?", (user_id,)
        )
        row = await cur.fetchone()
        return json.loads(row[0]) if row else None

    async def update_settings(self, user_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        current = await self.get_settings(user_id) or {}
        current.update(patch)
        blob = json.dumps(current)
        await self._conn().execute(
            """INSERT INTO user_settings (user_id, data) VALUES (?, ?)
               ON CONFLICT(user_id) DO UPDATE SET data = excluded.data""",
            (user_id, blob),
        )
        await self._conn().commit()
        return current

    async def get_profile(self, user_id: str) -> dict[str, Any] | None:
        cur = await self._conn().execute(
            "SELECT data FROM user_profiles WHERE user_id = ?", (user_id,)
        )
        row = await cur.fetchone()
        return json.loads(row[0]) if row else None

    async def upsert_profile(self, user_id: str, data: dict[str, Any]) -> dict[str, Any]:
        blob = json.dumps(data)
        await self._conn().execute(
            """INSERT INTO user_profiles (user_id, data) VALUES (?, ?)
               ON CONFLICT(user_id) DO UPDATE SET data = excluded.data""",
            (user_id, blob),
        )
        await self._conn().commit()
        return data
