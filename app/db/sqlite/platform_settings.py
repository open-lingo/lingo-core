"""SQLite-backed platform settings repository.

Single ``platform_settings`` table with ``(key, value_json)`` rows. Values
are stored as JSON strings to keep the table schema-free; consumers
deserialize whatever shape they expect.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS platform_settings (
    key         TEXT PRIMARY KEY,
    value_json  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class SqlitePlatformSettingsRepository:
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

    async def get(self, key: str) -> dict[str, Any] | None:
        cur = await self._conn().execute(
            "SELECT value_json FROM platform_settings WHERE key = ?",
            (key,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row["value_json"])
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    async def put(self, key: str, value: dict[str, Any]) -> dict[str, Any]:
        await self._conn().execute(
            """INSERT INTO platform_settings (key, value_json, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                   value_json = excluded.value_json,
                   updated_at = excluded.updated_at""",
            (key, json.dumps(value), _now_iso()),
        )
        await self._conn().commit()
        return dict(value)
