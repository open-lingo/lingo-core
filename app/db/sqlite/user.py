"""SQLite-backed user repository (local development).

The ``users`` table uses real columns so you can query by auth0_id,
username, or internal UUID directly.  Settings use the internal UUID as FK.
"""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import aiosqlite

_MIGRATION_COLS = [
    ("status_expiration", "TEXT"),
    ("community_status", "TEXT"),
    ("community_status_expiration", "TEXT"),
    ("bio", "TEXT"),
    ("role", "TEXT NOT NULL DEFAULT 'user'"),
]

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id                         TEXT PRIMARY KEY,
    auth0_id                   TEXT NOT NULL UNIQUE,
    username                   TEXT NOT NULL UNIQUE,
    display_name               TEXT NOT NULL,
    profile_picture_key        TEXT,
    status                     TEXT NOT NULL DEFAULT 'active',
    status_expiration          TEXT,
    community_status           TEXT,
    community_status_expiration TEXT,
    bio                        TEXT,
    role                       TEXT NOT NULL DEFAULT 'user',
    created_at                 TEXT NOT NULL,
    updated_at                 TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id TEXT PRIMARY KEY REFERENCES users(id),
    data    TEXT NOT NULL DEFAULT '{}'
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
        for col, col_type in _MIGRATION_COLS:
            try:
                await self._conn().execute(f"ALTER TABLE users ADD COLUMN {col} {col_type}")
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
            "role": user.get("role", "user"),
            "created_at": now,
            "updated_at": now,
        }
        await self._conn().execute(
            """INSERT INTO users (id, auth0_id, username, display_name,
                                  profile_picture_key, status, role, created_at, updated_at)
               VALUES (:id, :auth0_id, :username, :display_name,
                       :profile_picture_key, :status, :role, :created_at, :updated_at)""",
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

    async def update_user(self, user_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        current = await self.get_user_by_id(user_id)
        if current is None:
            raise LookupError(f"No user with id={user_id!r}")

        current.update(patch)
        current["updated_at"] = datetime.now(UTC).isoformat()
        for k in ("status_expiration", "community_status", "community_status_expiration", "bio", "role"):
            current.setdefault(k)

        await self._conn().execute(
            """UPDATE users
               SET username = :username,
                   display_name = :display_name,
                   profile_picture_key = :profile_picture_key,
                   status = :status,
                   status_expiration = :status_expiration,
                   community_status = :community_status,
                   community_status_expiration = :community_status_expiration,
                   bio = :bio,
                   role = :role,
                   updated_at = :updated_at
               WHERE id = :id""",
            current,
        )
        await self._conn().commit()
        return current

    async def list_users(
        self,
        limit: int = 100,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        offset = int(cursor) if cursor else 0
        cur = await self._conn().execute(
            "SELECT * FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit + 1, offset),
        )
        rows = await cur.fetchall()
        items = [dict(r) for r in rows[:limit]]
        next_cursor = str(offset + limit) if len(rows) > limit else None
        return items, next_cursor

    async def delete_user(self, user_id: str) -> None:
        await self._conn().execute("DELETE FROM user_settings WHERE user_id = ?", (user_id,))
        await self._conn().execute("DELETE FROM users WHERE id = ?", (user_id,))
        await self._conn().commit()

    # -- User settings --

    async def get_settings(self, user_id: str) -> dict[str, Any] | None:
        cur = await self._conn().execute(
            "SELECT data FROM user_settings WHERE user_id = ?", (user_id,)
        )
        row = await cur.fetchone()
        return json.loads(row["data"]) if row else None

    async def update_settings(self, user_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        def _deep_merge(base: dict[str, Any], update: dict[str, Any]) -> None:
            for k, v in update.items():
                if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                    _deep_merge(base[k], v)
                else:
                    base[k] = v

        current = await self.get_settings(user_id) or {}
        _deep_merge(current, patch)
        blob = json.dumps(current)
        await self._conn().execute(
            """INSERT INTO user_settings (user_id, data) VALUES (?, ?)
               ON CONFLICT(user_id) DO UPDATE SET data = excluded.data""",
            (user_id, blob),
        )
        await self._conn().commit()
        return current
