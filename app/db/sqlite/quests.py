"""SQLite-backed quest repository."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS quests (
    id                       TEXT PRIMARY KEY,
    user_id                  TEXT NOT NULL,
    type                     TEXT NOT NULL,
    title_key                TEXT NOT NULL,
    description_key          TEXT NOT NULL,
    emoji                    TEXT NOT NULL DEFAULT '',
    progress_current         INTEGER NOT NULL DEFAULT 0,
    progress_target          INTEGER NOT NULL,
    progress_unit            TEXT NOT NULL DEFAULT '',
    reward_lingots           INTEGER NOT NULL DEFAULT 0,
    reward_xp                INTEGER NOT NULL DEFAULT 0,
    reward_ad_free_minutes   INTEGER NOT NULL DEFAULT 0,
    reward_streak_shield     INTEGER NOT NULL DEFAULT 0,
    status                   TEXT NOT NULL DEFAULT 'active',
    friend_id                TEXT,
    friend_display_name      TEXT,
    expires_at               TEXT,
    reward_granted           INTEGER NOT NULL DEFAULT 0,
    created_at               TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_quests_user ON quests (user_id, created_at DESC);
"""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _row_to_quest(row: aiosqlite.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "type": row["type"],
        "title_key": row["title_key"],
        "description_key": row["description_key"],
        "emoji": row["emoji"],
        "progress_current": int(row["progress_current"]),
        "progress_target": int(row["progress_target"]),
        "progress_unit": row["progress_unit"],
        "reward_lingots": int(row["reward_lingots"]),
        "reward_xp": int(row["reward_xp"]),
        "reward_ad_free_minutes": int(row["reward_ad_free_minutes"]),
        "reward_streak_shield": bool(row["reward_streak_shield"]),
        "status": row["status"],
        "friend_id": row["friend_id"],
        "friend_display_name": row["friend_display_name"],
        "expires_at": row["expires_at"],
        "reward_granted": bool(row["reward_granted"]),
        "created_at": row["created_at"],
    }


class SqliteQuestRepository:
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

    async def list_quests(self, user_id: str) -> list[dict[str, Any]]:
        cur = await self._conn().execute(
            "SELECT * FROM quests WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )
        return [_row_to_quest(r) for r in await cur.fetchall()]

    async def get_quest(self, user_id: str, quest_id: str) -> dict[str, Any] | None:
        cur = await self._conn().execute(
            "SELECT * FROM quests WHERE user_id = ? AND id = ?",
            (user_id, quest_id),
        )
        row = await cur.fetchone()
        return _row_to_quest(row) if row else None

    async def put_quest(self, quest: dict[str, Any]) -> dict[str, Any]:
        created_at = quest.get("created_at") or _now_iso()
        await self._conn().execute(
            """INSERT INTO quests (
                id, user_id, type, title_key, description_key, emoji,
                progress_current, progress_target, progress_unit,
                reward_lingots, reward_xp, reward_ad_free_minutes,
                reward_streak_shield, status, friend_id, friend_display_name,
                expires_at, reward_granted, created_at
            ) VALUES (
                :id, :user_id, :type, :title_key, :description_key, :emoji,
                :progress_current, :progress_target, :progress_unit,
                :reward_lingots, :reward_xp, :reward_ad_free_minutes,
                :reward_streak_shield, :status, :friend_id, :friend_display_name,
                :expires_at, :reward_granted, :created_at
            )
            ON CONFLICT(id) DO UPDATE SET
                type = excluded.type,
                title_key = excluded.title_key,
                description_key = excluded.description_key,
                emoji = excluded.emoji,
                progress_current = excluded.progress_current,
                progress_target = excluded.progress_target,
                progress_unit = excluded.progress_unit,
                reward_lingots = excluded.reward_lingots,
                reward_xp = excluded.reward_xp,
                reward_ad_free_minutes = excluded.reward_ad_free_minutes,
                reward_streak_shield = excluded.reward_streak_shield,
                status = excluded.status,
                friend_id = excluded.friend_id,
                friend_display_name = excluded.friend_display_name,
                expires_at = excluded.expires_at,
                reward_granted = excluded.reward_granted
            """,
            {
                "id": quest["id"],
                "user_id": quest["user_id"],
                "type": quest["type"],
                "title_key": quest["title_key"],
                "description_key": quest["description_key"],
                "emoji": quest.get("emoji") or "",
                "progress_current": int(quest.get("progress_current") or 0),
                "progress_target": int(quest["progress_target"]),
                "progress_unit": quest.get("progress_unit") or "",
                "reward_lingots": int(quest.get("reward_lingots") or 0),
                "reward_xp": int(quest.get("reward_xp") or 0),
                "reward_ad_free_minutes": int(quest.get("reward_ad_free_minutes") or 0),
                "reward_streak_shield": 1 if quest.get("reward_streak_shield") else 0,
                "status": quest.get("status") or "active",
                "friend_id": quest.get("friend_id"),
                "friend_display_name": quest.get("friend_display_name"),
                "expires_at": quest.get("expires_at"),
                "reward_granted": 1 if quest.get("reward_granted") else 0,
                "created_at": created_at,
            },
        )
        await self._conn().commit()
        got = await self.get_quest(quest["user_id"], quest["id"])
        assert got is not None
        return got

    async def update_progress(
        self, user_id: str, quest_id: str, delta: int
    ) -> dict[str, Any] | None:
        current = await self.get_quest(user_id, quest_id)
        if current is None:
            return None
        if current["status"] in ("completed", "expired"):
            return current
        new_current = max(0, min(current["progress_target"], current["progress_current"] + delta))
        new_status = current["status"]
        if new_current >= current["progress_target"] and new_status == "active":
            new_status = "claimable"
        await self._conn().execute(
            """UPDATE quests
               SET progress_current = ?, status = ?
               WHERE user_id = ? AND id = ?""",
            (new_current, new_status, user_id, quest_id),
        )
        await self._conn().commit()
        return await self.get_quest(user_id, quest_id)

    async def claim(
        self, user_id: str, quest_id: str
    ) -> dict[str, Any] | None:
        current = await self.get_quest(user_id, quest_id)
        if current is None:
            return None
        if current["status"] == "completed":
            return current
        if current["status"] != "claimable":
            return None
        await self._conn().execute(
            """UPDATE quests
               SET status = 'completed', reward_granted = 1
               WHERE user_id = ? AND id = ?""",
            (user_id, quest_id),
        )
        await self._conn().commit()
        return await self.get_quest(user_id, quest_id)

    async def delete_user_quests(
        self, user_id: str, types: list[str] | None = None
    ) -> int:
        if types:
            placeholders = ",".join("?" * len(types))
            cur = await self._conn().execute(
                f"DELETE FROM quests WHERE user_id = ? AND type IN ({placeholders})",
                (user_id, *types),
            )
        else:
            cur = await self._conn().execute(
                "DELETE FROM quests WHERE user_id = ?",
                (user_id,),
            )
        await self._conn().commit()
        return cur.rowcount or 0
