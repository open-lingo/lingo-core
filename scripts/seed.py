"""Seed the local SQLite database with test data.

Usage:
    python -m scripts.seed            # seed (skip existing)
    python -m scripts.seed --reset    # wipe everything and re-seed
"""

import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime

import aiosqlite

from app.config import settings

DEV_USER = settings.DEV_USER

SEED_USERS = [
    {
        "auth0_id": DEV_USER,
        "username": "trevor",
        "display_name": "Trevor",
        "profile_picture_key": None,
        "status": "active",
    },
    {
        "auth0_id": "dev|user-2",
        "username": "hana",
        "display_name": "Hana Kim",
        "profile_picture_key": None,
        "status": "active",
    },
    {
        "auth0_id": "dev|user-3",
        "username": "testuser",
        "display_name": "Test User",
        "profile_picture_key": None,
        "status": "active",
    },
]

SEED_SETTINGS = {
    DEV_USER: {"theme": "dark", "learningLanguage": "ko", "uiLocale": "en"},
    "dev|user-2": {"theme": "light", "learningLanguage": "en", "uiLocale": "ko"},
    "dev|user-3": {"theme": "system", "learningLanguage": "ko", "uiLocale": "en"},
}

INIT_SQL = """
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


async def reset(db: aiosqlite.Connection) -> None:
    print("  Dropping tables...")
    await db.execute("DROP TABLE IF EXISTS user_settings")
    await db.execute("DROP TABLE IF EXISTS users")
    await db.commit()


async def seed(db_path: str, do_reset: bool) -> None:
    print(f"Database: {db_path}")
    db = await aiosqlite.connect(db_path)

    if do_reset:
        await reset(db)

    await db.executescript(INIT_SQL)

    now = datetime.now(UTC).isoformat()
    created = 0
    skipped = 0

    for u in SEED_USERS:
        cur = await db.execute("SELECT 1 FROM users WHERE auth0_id = ?", (u["auth0_id"],))
        if await cur.fetchone():
            skipped += 1
            continue

        await db.execute(
            """INSERT INTO users (id, auth0_id, username, display_name,
                                  profile_picture_key, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                u["auth0_id"],
                u["username"],
                u["display_name"],
                u["profile_picture_key"],
                u["status"],
                now,
                now,
            ),
        )
        created += 1

    settings_created = 0
    for auth0_id, prefs in SEED_SETTINGS.items():
        cur = await db.execute("SELECT 1 FROM user_settings WHERE auth0_id = ?", (auth0_id,))
        if await cur.fetchone():
            continue
        await db.execute(
            "INSERT INTO user_settings (auth0_id, data) VALUES (?, ?)",
            (auth0_id, json.dumps(prefs)),
        )
        settings_created += 1

    await db.commit()
    await db.close()

    print(f"  Users:    {created} created, {skipped} skipped (already exist)")
    print(f"  Settings: {settings_created} created")
    print("Done.")


if __name__ == "__main__":
    do_reset = "--reset" in sys.argv
    asyncio.run(seed(settings.SQLITE_PATH, do_reset))
