"""SQLite-backed repository for local development.

The ``users`` table uses real columns (mirrors the DynamoDB item attributes)
so you can query by auth0_id, username, or internal id directly.
Settings stay as a JSON blob since they're schema-flexible.
"""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import aiosqlite

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id                  TEXT PRIMARY KEY,
    auth0_id            TEXT NOT NULL UNIQUE,
    username            TEXT NOT NULL UNIQUE,
    display_name        TEXT NOT NULL,
    profile_picture_key TEXT,
    status              TEXT NOT NULL DEFAULT 'active',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_settings (
    auth0_id TEXT PRIMARY KEY,
    data     TEXT NOT NULL DEFAULT '{}'
);
"""


class SqliteUserRepository:
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

    # -- User record --

    async def create_user(self, user: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        row = {
            "id": str(uuid.uuid4()),
            "auth0_id": user["auth0_id"],
            "username": user["username"],
            "display_name": user["display_name"],
            "profile_picture_key": user.get("profile_picture_key"),
            "status": user.get("status", "active"),
            "created_at": now,
            "updated_at": now,
        }
        await self._conn().execute(
            """INSERT INTO users (id, auth0_id, username, display_name,
                                  profile_picture_key, status, created_at, updated_at)
               VALUES (:id, :auth0_id, :username, :display_name,
                       :profile_picture_key, :status, :created_at, :updated_at)""",
            row,
        )
        await self._conn().commit()
        return row

    async def get_user_by_auth0_id(self, auth0_id: str) -> dict[str, Any] | None:
        cur = await self._conn().execute("SELECT * FROM users WHERE auth0_id = ?", (auth0_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        cur = await self._conn().execute("SELECT * FROM users WHERE id = ?", (user_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        cur = await self._conn().execute("SELECT * FROM users WHERE username = ?", (username,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def update_user(self, auth0_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        current = await self.get_user_by_auth0_id(auth0_id)
        if current is None:
            raise LookupError(f"No user with auth0_id={auth0_id!r}")

        current.update(patch)
        current["updated_at"] = datetime.now(UTC).isoformat()

        await self._conn().execute(
            """UPDATE users
               SET username = :username,
                   display_name = :display_name,
                   profile_picture_key = :profile_picture_key,
                   status = :status,
                   updated_at = :updated_at
               WHERE auth0_id = :auth0_id""",
            current,
        )
        await self._conn().commit()
        return current

    # -- User settings --

    async def get_settings(self, auth0_id: str) -> dict[str, Any] | None:
        cur = await self._conn().execute(
            "SELECT data FROM user_settings WHERE auth0_id = ?", (auth0_id,)
        )
        row = await cur.fetchone()
        return json.loads(row["data"]) if row else None

    async def update_settings(self, auth0_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        current = await self.get_settings(auth0_id) or {}
        current.update(patch)
        blob = json.dumps(current)
        await self._conn().execute(
            """INSERT INTO user_settings (auth0_id, data) VALUES (?, ?)
               ON CONFLICT(auth0_id) DO UPDATE SET data = excluded.data""",
            (auth0_id, blob),
        )
        await self._conn().commit()
        return current
