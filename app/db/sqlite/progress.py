"""SQLite-backed progress repository (local development).

Mirrors the DynamoDB single-table layout: one user can have many rows across
four SK shapes (ATTEMPT / LESSON / DAY / CONCEPT). For SQLite we split that
into normalized tables for clarity, but the protocol surface is identical.

See ``docs/adr/0001-progress-api-hybrid-rollup.md`` for the data model and
write flow. The repo is intentionally minimal: it stores rows, it doesn't
make policy decisions (the router owns idempotency / rate-limit / streak
gating / XP arithmetic).
"""

import json
from typing import Any

import aiosqlite

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS progress_attempts (
    user_id            TEXT NOT NULL,
    attempt_id         TEXT NOT NULL,
    lesson_id          TEXT NOT NULL,
    attempted_at       TEXT NOT NULL,
    client_attempt_id  TEXT NOT NULL,
    duration_sec       INTEGER NOT NULL,
    passed             INTEGER NOT NULL,
    score              REAL NOT NULL,
    steps_json         TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (user_id, attempt_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_progress_client_id
    ON progress_attempts (user_id, client_attempt_id);
CREATE INDEX IF NOT EXISTS idx_progress_user_attempted
    ON progress_attempts (user_id, attempted_at DESC);
CREATE INDEX IF NOT EXISTS idx_progress_user_lesson_attempted
    ON progress_attempts (user_id, lesson_id, attempted_at DESC);

CREATE TABLE IF NOT EXISTS progress_lesson_rollups (
    user_id            TEXT NOT NULL,
    lesson_id          TEXT NOT NULL,
    best_score         REAL NOT NULL DEFAULT 0,
    first_passed_at    TEXT,
    latest_attempt_at  TEXT NOT NULL,
    attempt_count      INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (user_id, lesson_id)
);

CREATE TABLE IF NOT EXISTS progress_day_rollups (
    user_id            TEXT NOT NULL,
    date               TEXT NOT NULL,
    lessons_completed  INTEGER NOT NULL DEFAULT 0,
    minutes_active     INTEGER NOT NULL DEFAULT 0,
    xp_earned          INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, date)
);

CREATE TABLE IF NOT EXISTS progress_concept_rollups (
    user_id            TEXT NOT NULL,
    concept_id         TEXT NOT NULL,
    encounters         INTEGER NOT NULL DEFAULT 0,
    correct_count      INTEGER NOT NULL DEFAULT 0,
    incorrect_count    INTEGER NOT NULL DEFAULT 0,
    recent_results     TEXT NOT NULL DEFAULT '[]',
    avg_duration_ms    INTEGER,
    first_seen_at      TEXT NOT NULL,
    last_seen_at       TEXT NOT NULL,
    last_correct_at    TEXT,
    stale_at           TEXT,
    PRIMARY KEY (user_id, concept_id)
);
"""


def _attempt_row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
    return {
        "attemptId": row["attempt_id"],
        "clientAttemptId": row["client_attempt_id"],
        "lessonId": row["lesson_id"],
        "attemptedAt": row["attempted_at"],
        "durationSec": row["duration_sec"],
        "passed": bool(row["passed"]),
        "score": row["score"],
        "steps": json.loads(row["steps_json"]),
    }


def _lesson_row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
    return {
        "lessonId": row["lesson_id"],
        "bestScore": row["best_score"],
        "firstPassedAt": row["first_passed_at"],
        "latestAttemptAt": row["latest_attempt_at"],
        "attemptCount": row["attempt_count"],
    }


def _day_row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
    return {
        "date": row["date"],
        "lessonsCompleted": row["lessons_completed"],
        "minutesActive": row["minutes_active"],
        "xpEarned": row["xp_earned"],
    }


def _concept_row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
    return {
        "conceptId": row["concept_id"],
        "encounters": row["encounters"],
        "correctCount": row["correct_count"],
        "incorrectCount": row["incorrect_count"],
        "recentResults": json.loads(row["recent_results"]),
        "avgDurationMs": row["avg_duration_ms"],
        "firstSeenAt": row["first_seen_at"],
        "lastSeenAt": row["last_seen_at"],
        "lastCorrectAt": row["last_correct_at"],
        "staleAt": row["stale_at"],
    }


class SqliteProgressRepository:
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

    # ── Attempt log ─────────────────────────────────────────────────────────

    async def put_attempt(self, user_id: str, attempt: dict[str, Any]) -> None:
        steps_json = json.dumps(attempt.get("steps", []))
        # INSERT OR IGNORE — idempotency on (user_id, client_attempt_id) via the
        # unique index. Re-pushing the same client attempt is a no-op.
        await self._conn().execute(
            """
            INSERT OR IGNORE INTO progress_attempts (
                user_id, attempt_id, lesson_id, attempted_at, client_attempt_id,
                duration_sec, passed, score, steps_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                attempt["attemptId"],
                attempt["lessonId"],
                attempt["attemptedAt"],
                attempt["clientAttemptId"],
                int(attempt["durationSec"]),
                1 if attempt["passed"] else 0,
                float(attempt["score"]),
                steps_json,
            ),
        )
        await self._conn().commit()

    async def attempt_exists(self, user_id: str, client_attempt_id: str) -> dict[str, Any] | None:
        """Idempotency lookup. Returns the existing attempt row if present."""
        cur = await self._conn().execute(
            "SELECT * FROM progress_attempts WHERE user_id = ? AND client_attempt_id = ?",
            (user_id, client_attempt_id),
        )
        row = await cur.fetchone()
        return _attempt_row_to_dict(row) if row else None

    async def list_attempts(
        self,
        user_id: str,
        lesson_id: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        params: list[Any] = [user_id]
        sql = "SELECT * FROM progress_attempts WHERE user_id = ?"
        if lesson_id:
            sql += " AND lesson_id = ?"
            params.append(lesson_id)
        if cursor:
            sql += " AND attempted_at < ?"
            params.append(cursor)
        sql += " ORDER BY attempted_at DESC LIMIT ?"
        params.append(limit + 1)

        cur = await self._conn().execute(sql, params)
        rows = await cur.fetchall()
        items = [_attempt_row_to_dict(r) for r in rows[:limit]]
        next_cursor = items[-1]["attemptedAt"] if len(rows) > limit else None
        return items, next_cursor

    async def get_attempts_for_concepts(
        self,
        user_id: str,
        concept_ids: list[str],
        since: str | None = None,
    ) -> list[dict[str, Any]]:
        if not concept_ids:
            return []
        # Server-side filter on concept membership via JSON scan. Cheap at
        # local-dev scale; the Dynamo impl will use a GSI query.
        params: list[Any] = [user_id]
        sql = "SELECT * FROM progress_attempts WHERE user_id = ?"
        if since:
            sql += " AND attempted_at >= ?"
            params.append(since)
        sql += " ORDER BY attempted_at DESC"
        cur = await self._conn().execute(sql, params)
        rows = await cur.fetchall()
        out: list[dict[str, Any]] = []
        concept_set = set(concept_ids)
        for row in rows:
            attempt = _attempt_row_to_dict(row)
            if any(
                cid in concept_set
                for step in attempt["steps"]
                for cid in (step.get("conceptIds") or [])
            ):
                out.append(attempt)
        return out

    # ── Lesson rollups ──────────────────────────────────────────────────────

    async def update_lesson_rollup(
        self, user_id: str, lesson_id: str, attempt: dict[str, Any]
    ) -> dict[str, Any]:
        score = float(attempt["score"])
        attempted_at = attempt["attemptedAt"]
        passed = bool(attempt["passed"])
        await self._conn().execute(
            """
            INSERT INTO progress_lesson_rollups (
                user_id, lesson_id, best_score, first_passed_at,
                latest_attempt_at, attempt_count
            )
            VALUES (?, ?, ?, ?, ?, 1)
            ON CONFLICT(user_id, lesson_id) DO UPDATE SET
                best_score = MAX(best_score, excluded.best_score),
                first_passed_at = COALESCE(first_passed_at, excluded.first_passed_at),
                latest_attempt_at = excluded.latest_attempt_at,
                attempt_count = attempt_count + 1
            """,
            (
                user_id,
                lesson_id,
                score,
                attempted_at if passed else None,
                attempted_at,
            ),
        )
        await self._conn().commit()
        cur = await self._conn().execute(
            "SELECT * FROM progress_lesson_rollups WHERE user_id = ? AND lesson_id = ?",
            (user_id, lesson_id),
        )
        row = await cur.fetchone()
        assert row is not None
        return _lesson_row_to_dict(row)

    async def get_lesson_rollups(self, user_id: str) -> list[dict[str, Any]]:
        cur = await self._conn().execute(
            "SELECT * FROM progress_lesson_rollups WHERE user_id = ?",
            (user_id,),
        )
        rows = await cur.fetchall()
        return [_lesson_row_to_dict(r) for r in rows]

    # ── Day rollups ─────────────────────────────────────────────────────────

    async def update_day_rollup(
        self,
        user_id: str,
        date: str,
        lessons_inc: int,
        minutes_inc: int,
        xp_inc: int,
    ) -> dict[str, Any]:
        await self._conn().execute(
            """
            INSERT INTO progress_day_rollups (
                user_id, date, lessons_completed, minutes_active, xp_earned
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, date) DO UPDATE SET
                lessons_completed = lessons_completed + excluded.lessons_completed,
                minutes_active = minutes_active + excluded.minutes_active,
                xp_earned = xp_earned + excluded.xp_earned
            """,
            (user_id, date, lessons_inc, minutes_inc, xp_inc),
        )
        await self._conn().commit()
        cur = await self._conn().execute(
            "SELECT * FROM progress_day_rollups WHERE user_id = ? AND date = ?",
            (user_id, date),
        )
        row = await cur.fetchone()
        assert row is not None
        return _day_row_to_dict(row)

    async def get_day_rollups(
        self, user_id: str, since: str, until: str
    ) -> list[dict[str, Any]]:
        cur = await self._conn().execute(
            """
            SELECT * FROM progress_day_rollups
            WHERE user_id = ? AND date BETWEEN ? AND ?
            ORDER BY date
            """,
            (user_id, since, until),
        )
        rows = await cur.fetchall()
        return [_day_row_to_dict(r) for r in rows]

    # ── Concept rollups ─────────────────────────────────────────────────────

    async def invalidate_concepts(
        self, user_id: str, concept_ids: list[str], staleAt: str
    ) -> None:
        for cid in concept_ids:
            await self._conn().execute(
                """
                INSERT INTO progress_concept_rollups (
                    user_id, concept_id, encounters, correct_count, incorrect_count,
                    recent_results, first_seen_at, last_seen_at, stale_at
                )
                VALUES (?, ?, 0, 0, 0, '[]', ?, ?, ?)
                ON CONFLICT(user_id, concept_id) DO UPDATE SET
                    stale_at = excluded.stale_at,
                    last_seen_at = excluded.last_seen_at
                """,
                (user_id, cid, staleAt, staleAt, staleAt),
            )
        await self._conn().commit()

    async def get_concept_rollups(self, user_id: str) -> list[dict[str, Any]]:
        cur = await self._conn().execute(
            "SELECT * FROM progress_concept_rollups WHERE user_id = ?",
            (user_id,),
        )
        rows = await cur.fetchall()
        return [_concept_row_to_dict(r) for r in rows]

    async def put_concept_rollup(
        self, user_id: str, rollup: dict[str, Any]
    ) -> None:
        await self._conn().execute(
            """
            INSERT INTO progress_concept_rollups (
                user_id, concept_id, encounters, correct_count, incorrect_count,
                recent_results, avg_duration_ms, first_seen_at, last_seen_at,
                last_correct_at, stale_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(user_id, concept_id) DO UPDATE SET
                encounters = excluded.encounters,
                correct_count = excluded.correct_count,
                incorrect_count = excluded.incorrect_count,
                recent_results = excluded.recent_results,
                avg_duration_ms = excluded.avg_duration_ms,
                last_seen_at = excluded.last_seen_at,
                last_correct_at = excluded.last_correct_at,
                stale_at = NULL
            """,
            (
                user_id,
                rollup["conceptId"],
                rollup.get("encounters", 0),
                rollup.get("correctCount", 0),
                rollup.get("incorrectCount", 0),
                json.dumps(rollup.get("recentResults", [])),
                rollup.get("avgDurationMs"),
                rollup["firstSeenAt"],
                rollup["lastSeenAt"],
                rollup.get("lastCorrectAt"),
            ),
        )
        await self._conn().commit()
