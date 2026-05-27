"""SQLite-backed social repository (local development).

Stores friends, friend requests, blocks, activity items + reactions, invite
codes + redemptions, and threads + messages. Each table is created lazily on
``connect()``.

The Dynamo impl mirrors this layout under a single-table design — see
``app/db/dynamo/social.py``.
"""

import json
from datetime import UTC, datetime
from typing import Any

import aiosqlite

_INIT_SQL = """
-- Friend edges (reciprocal — two rows per friendship for cheap lookup).
CREATE TABLE IF NOT EXISTS social_friends (
    user_id     TEXT NOT NULL,
    friend_id   TEXT NOT NULL,
    friended_at TEXT NOT NULL,
    PRIMARY KEY (user_id, friend_id)
);
CREATE INDEX IF NOT EXISTS idx_social_friends_user ON social_friends (user_id);

-- Friend requests: a single row per directed pending request.
CREATE TABLE IF NOT EXISTS social_friend_requests (
    from_id      TEXT NOT NULL,
    to_id        TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    PRIMARY KEY (from_id, to_id)
);
CREATE INDEX IF NOT EXISTS idx_social_friend_requests_to ON social_friend_requests (to_id);
CREATE INDEX IF NOT EXISTS idx_social_friend_requests_from ON social_friend_requests (from_id);

CREATE TABLE IF NOT EXISTS social_blocks (
    blocker_id TEXT NOT NULL,
    blocked_id TEXT NOT NULL,
    blocked_at TEXT NOT NULL,
    PRIMARY KEY (blocker_id, blocked_id)
);

-- Activity feed: one row per emitted event. ``payload_json`` keeps the body
-- flexible (lesson id, xp earned, milestone day count, etc.).
CREATE TABLE IF NOT EXISTS social_activity (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    kind          TEXT NOT NULL,
    payload_json  TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_social_activity_user_time
    ON social_activity (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_social_activity_time
    ON social_activity (created_at DESC);

-- Reactions: a row per (activity, user, kind).
CREATE TABLE IF NOT EXISTS social_activity_reactions (
    activity_id TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    kind        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (activity_id, user_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_social_activity_reactions_activity
    ON social_activity_reactions (activity_id);

-- Invite codes: one persistent code per owner. Generated lazily on first /offer.
CREATE TABLE IF NOT EXISTS social_invite_codes (
    code        TEXT PRIMARY KEY,
    owner_id    TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL
);

-- Invite redemptions: status transitions from pending → redeemed once the
-- invitee completes first lesson. ``self`` / ``cap_reached`` / ``invalid``
-- never persist except for diagnostics — we don't store them here.
CREATE TABLE IF NOT EXISTS social_invite_redemptions (
    code         TEXT NOT NULL,
    invitee_id   TEXT NOT NULL,
    inviter_id   TEXT NOT NULL,
    status       TEXT NOT NULL,
    redeemed_at  TEXT NOT NULL,
    year_month   TEXT NOT NULL,
    PRIMARY KEY (code, invitee_id)
);
CREATE INDEX IF NOT EXISTS idx_social_invite_redemptions_inviter_month
    ON social_invite_redemptions (inviter_id, year_month);

-- Threads + messages (stub messaging — reads only).
CREATE TABLE IF NOT EXISTS social_threads (
    id          TEXT PRIMARY KEY,
    user_a_id   TEXT NOT NULL,
    user_b_id   TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_social_threads_user_a ON social_threads (user_a_id);
CREATE INDEX IF NOT EXISTS idx_social_threads_user_b ON social_threads (user_b_id);

CREATE TABLE IF NOT EXISTS social_messages (
    id         TEXT PRIMARY KEY,
    thread_id  TEXT NOT NULL,
    sender_id  TEXT NOT NULL,
    body       TEXT NOT NULL,
    sent_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_social_messages_thread_time
    ON social_messages (thread_id, sent_at);
"""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _year_month(iso: str) -> str:
    return iso[:7]


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

    # ── Friends ──────────────────────────────────────────────────────────────

    async def list_friends(self, user_id: str) -> list[dict[str, Any]]:
        cur = await self._conn().execute(
            "SELECT friend_id, friended_at FROM social_friends WHERE user_id = ?",
            (user_id,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def is_friend(self, user_id: str, other_id: str) -> bool:
        cur = await self._conn().execute(
            "SELECT 1 FROM social_friends WHERE user_id = ? AND friend_id = ?",
            (user_id, other_id),
        )
        return (await cur.fetchone()) is not None

    async def add_friend_edge(self, a_id: str, b_id: str) -> None:
        if a_id == b_id:
            return
        now = _now_iso()
        for x, y in ((a_id, b_id), (b_id, a_id)):
            await self._conn().execute(
                """INSERT INTO social_friends (user_id, friend_id, friended_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(user_id, friend_id) DO NOTHING""",
                (x, y, now),
            )
        await self._conn().commit()

    async def remove_friend_edge(self, a_id: str, b_id: str) -> None:
        await self._conn().execute(
            """DELETE FROM social_friends
               WHERE (user_id = ? AND friend_id = ?) OR (user_id = ? AND friend_id = ?)""",
            (a_id, b_id, b_id, a_id),
        )
        await self._conn().commit()

    # ── Friend requests ──────────────────────────────────────────────────────

    async def list_friend_requests(self, user_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        cur = await self._conn().execute(
            "SELECT from_id, to_id, requested_at FROM social_friend_requests WHERE to_id = ?",
            (user_id,),
        )
        incoming = [dict(r) for r in await cur.fetchall()]
        cur = await self._conn().execute(
            "SELECT from_id, to_id, requested_at FROM social_friend_requests WHERE from_id = ?",
            (user_id,),
        )
        outgoing = [dict(r) for r in await cur.fetchall()]
        return incoming, outgoing

    async def get_friend_request(self, from_id: str, to_id: str) -> dict[str, Any] | None:
        cur = await self._conn().execute(
            """SELECT from_id, to_id, requested_at FROM social_friend_requests
               WHERE from_id = ? AND to_id = ?""",
            (from_id, to_id),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def upsert_friend_request(self, from_id: str, to_id: str) -> dict[str, Any]:
        now = _now_iso()
        await self._conn().execute(
            """INSERT INTO social_friend_requests (from_id, to_id, requested_at)
               VALUES (?, ?, ?)
               ON CONFLICT(from_id, to_id) DO NOTHING""",
            (from_id, to_id, now),
        )
        await self._conn().commit()
        row = await self.get_friend_request(from_id, to_id)
        assert row is not None
        return row

    async def delete_friend_request(self, from_id: str, to_id: str) -> None:
        await self._conn().execute(
            "DELETE FROM social_friend_requests WHERE from_id = ? AND to_id = ?",
            (from_id, to_id),
        )
        await self._conn().commit()

    # ── Blocks ───────────────────────────────────────────────────────────────

    async def list_blocks(self, user_id: str) -> list[dict[str, Any]]:
        cur = await self._conn().execute(
            "SELECT blocked_id, blocked_at FROM social_blocks WHERE blocker_id = ?",
            (user_id,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def is_blocked(self, blocker_id: str, blocked_id: str) -> bool:
        cur = await self._conn().execute(
            "SELECT 1 FROM social_blocks WHERE blocker_id = ? AND blocked_id = ?",
            (blocker_id, blocked_id),
        )
        return (await cur.fetchone()) is not None

    async def block_user(self, blocker_id: str, blocked_id: str) -> None:
        if blocker_id == blocked_id:
            return
        await self._conn().execute(
            """INSERT INTO social_blocks (blocker_id, blocked_id, blocked_at)
               VALUES (?, ?, ?) ON CONFLICT(blocker_id, blocked_id) DO NOTHING""",
            (blocker_id, blocked_id, _now_iso()),
        )
        await self._conn().commit()

    async def unblock_user(self, blocker_id: str, blocked_id: str) -> None:
        await self._conn().execute(
            "DELETE FROM social_blocks WHERE blocker_id = ? AND blocked_id = ?",
            (blocker_id, blocked_id),
        )
        await self._conn().commit()

    # ── Activity feed ────────────────────────────────────────────────────────

    async def list_activity(
        self,
        user_id: str,
        friend_ids: list[str],
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        ids = [user_id, *friend_ids]
        placeholders = ",".join("?" * len(ids))
        params: list[Any] = list(ids)
        sql = f"""SELECT id, user_id, kind, payload_json, created_at
                  FROM social_activity
                  WHERE user_id IN ({placeholders})"""
        if cursor:
            sql += " AND created_at < ?"
            params.append(cursor)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit + 1)
        cur = await self._conn().execute(sql, params)
        rows = await cur.fetchall()
        items = [_activity_row_to_dict(r) for r in rows[:limit]]
        next_cursor = items[-1]["created_at"] if len(rows) > limit else None
        return items, next_cursor

    async def get_activity(self, activity_id: str) -> dict[str, Any] | None:
        cur = await self._conn().execute(
            """SELECT id, user_id, kind, payload_json, created_at
               FROM social_activity WHERE id = ?""",
            (activity_id,),
        )
        row = await cur.fetchone()
        return _activity_row_to_dict(row) if row else None

    async def put_activity(self, activity: dict[str, Any]) -> dict[str, Any]:
        created_at = activity.get("created_at") or _now_iso()
        await self._conn().execute(
            """INSERT INTO social_activity (id, user_id, kind, payload_json, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   kind = excluded.kind,
                   payload_json = excluded.payload_json,
                   created_at = excluded.created_at""",
            (
                activity["id"],
                activity["user_id"],
                activity["kind"],
                json.dumps(activity.get("payload") or {}),
                created_at,
            ),
        )
        await self._conn().commit()
        out = await self.get_activity(activity["id"])
        assert out is not None
        return out

    async def list_reactions(self, activity_id: str) -> list[dict[str, Any]]:
        cur = await self._conn().execute(
            """SELECT activity_id, user_id, kind, created_at
               FROM social_activity_reactions WHERE activity_id = ?""",
            (activity_id,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def list_reactions_bulk(self, activity_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {aid: [] for aid in activity_ids}
        if not activity_ids:
            return out
        placeholders = ",".join("?" * len(activity_ids))
        cur = await self._conn().execute(
            f"""SELECT activity_id, user_id, kind, created_at
                FROM social_activity_reactions
                WHERE activity_id IN ({placeholders})""",
            activity_ids,
        )
        for row in await cur.fetchall():
            out.setdefault(row["activity_id"], []).append(dict(row))
        return out

    async def toggle_reaction(self, activity_id: str, user_id: str, kind: str) -> tuple[bool, int]:
        cur = await self._conn().execute(
            """SELECT 1 FROM social_activity_reactions
               WHERE activity_id = ? AND user_id = ? AND kind = ?""",
            (activity_id, user_id, kind),
        )
        existed = (await cur.fetchone()) is not None
        if existed:
            await self._conn().execute(
                """DELETE FROM social_activity_reactions
                   WHERE activity_id = ? AND user_id = ? AND kind = ?""",
                (activity_id, user_id, kind),
            )
        else:
            await self._conn().execute(
                """INSERT INTO social_activity_reactions
                   (activity_id, user_id, kind, created_at)
                   VALUES (?, ?, ?, ?)""",
                (activity_id, user_id, kind, _now_iso()),
            )
        await self._conn().commit()
        cur = await self._conn().execute(
            """SELECT COUNT(*) AS n FROM social_activity_reactions
               WHERE activity_id = ? AND kind = ?""",
            (activity_id, kind),
        )
        row = await cur.fetchone()
        count_after = int(row["n"]) if row else 0
        return (not existed, count_after)

    # ── Invite codes / redemptions ───────────────────────────────────────────

    async def get_invite_code_for_owner(self, owner_id: str) -> dict[str, Any] | None:
        cur = await self._conn().execute(
            "SELECT code, owner_id, created_at FROM social_invite_codes WHERE owner_id = ?",
            (owner_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def create_invite_code(self, owner_id: str, code: str) -> dict[str, Any]:
        now = _now_iso()
        await self._conn().execute(
            """INSERT INTO social_invite_codes (code, owner_id, created_at)
               VALUES (?, ?, ?)
               ON CONFLICT(owner_id) DO NOTHING""",
            (code, owner_id, now),
        )
        await self._conn().commit()
        existing = await self.get_invite_code_for_owner(owner_id)
        assert existing is not None
        return existing

    async def get_invite_code(self, code: str) -> dict[str, Any] | None:
        cur = await self._conn().execute(
            "SELECT code, owner_id, created_at FROM social_invite_codes WHERE code = ?",
            (code,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def count_redemptions_for_owner_in_month(self, owner_id: str, year_month: str) -> int:
        cur = await self._conn().execute(
            """SELECT COUNT(*) AS n FROM social_invite_redemptions
               WHERE inviter_id = ? AND year_month = ? AND status != 'invalid'""",
            (owner_id, year_month),
        )
        row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def get_redemption(self, code: str, invitee_id: str) -> dict[str, Any] | None:
        cur = await self._conn().execute(
            """SELECT code, invitee_id, inviter_id, status, redeemed_at, year_month
               FROM social_invite_redemptions WHERE code = ? AND invitee_id = ?""",
            (code, invitee_id),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def upsert_redemption(self, redemption: dict[str, Any]) -> dict[str, Any]:
        ts = redemption.get("redeemed_at") or _now_iso()
        ym = redemption.get("year_month") or _year_month(ts)
        await self._conn().execute(
            """INSERT INTO social_invite_redemptions
                   (code, invitee_id, inviter_id, status, redeemed_at, year_month)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(code, invitee_id) DO UPDATE SET
                   status = excluded.status,
                   redeemed_at = excluded.redeemed_at""",
            (
                redemption["code"],
                redemption["invitee_id"],
                redemption["inviter_id"],
                redemption["status"],
                ts,
                ym,
            ),
        )
        await self._conn().commit()
        out = await self.get_redemption(redemption["code"], redemption["invitee_id"])
        assert out is not None
        return out

    # ── Threads / messages ───────────────────────────────────────────────────

    async def list_threads_for_user(self, user_id: str) -> list[dict[str, Any]]:
        cur = await self._conn().execute(
            """SELECT id, user_a_id, user_b_id, created_at, updated_at
               FROM social_threads
               WHERE user_a_id = ? OR user_b_id = ?
               ORDER BY updated_at DESC""",
            (user_id, user_id),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def get_thread(self, thread_id: str) -> dict[str, Any] | None:
        cur = await self._conn().execute(
            """SELECT id, user_a_id, user_b_id, created_at, updated_at
               FROM social_threads WHERE id = ?""",
            (thread_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def put_thread(self, thread: dict[str, Any]) -> dict[str, Any]:
        now = _now_iso()
        await self._conn().execute(
            """INSERT INTO social_threads (id, user_a_id, user_b_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   updated_at = excluded.updated_at""",
            (
                thread["id"],
                thread["user_a_id"],
                thread["user_b_id"],
                thread.get("created_at") or now,
                thread.get("updated_at") or now,
            ),
        )
        await self._conn().commit()
        out = await self.get_thread(thread["id"])
        assert out is not None
        return out

    async def list_messages(self, thread_id: str) -> list[dict[str, Any]]:
        cur = await self._conn().execute(
            """SELECT id, thread_id, sender_id, body, sent_at
               FROM social_messages WHERE thread_id = ?
               ORDER BY sent_at""",
            (thread_id,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def put_message(self, message: dict[str, Any]) -> dict[str, Any]:
        ts = message.get("sent_at") or _now_iso()
        await self._conn().execute(
            """INSERT INTO social_messages (id, thread_id, sender_id, body, sent_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(id) DO NOTHING""",
            (message["id"], message["thread_id"], message["sender_id"], message["body"], ts),
        )
        # Bump parent thread's updated_at so threads sort by recency.
        await self._conn().execute(
            "UPDATE social_threads SET updated_at = ? WHERE id = ?",
            (ts, message["thread_id"]),
        )
        await self._conn().commit()
        cur = await self._conn().execute(
            """SELECT id, thread_id, sender_id, body, sent_at
               FROM social_messages WHERE id = ?""",
            (message["id"],),
        )
        row = await cur.fetchone()
        assert row is not None
        return dict(row)


def _activity_row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "kind": row["kind"],
        "payload": json.loads(row["payload_json"]) if row["payload_json"] else {},
        "created_at": row["created_at"],
    }
