"""ProgressRepository protocol.

Single domain interface for the progress data model:

  ATTEMPT#<lessonId>#<isoTs>  — immutable per-attempt log
  LESSON#<lessonId>           — eager best-score rollup
  DAY#<YYYY-MM-DD>            — eager daily activity rollup
  CONCEPT#<conceptId>         — lazy-materialized mastery rollup (staleAt-flagged)

User-row stats (streak/XP/lingots) live on the existing UserRepository;
this protocol covers only the per-attempt log + derived per-user rollups.

See ADR-0001 for the full data model and lifecycle.
"""

from typing import Any, Protocol


class ProgressRepository(Protocol):
    """Per-user progress tracking. Backed by either SQLite (dev) or DynamoDB (prod)."""

    # ── Attempt log ──────────────────────────────────────────────────────────

    async def put_attempt(
        self, user_id: str, attempt: dict[str, Any]
    ) -> None:
        """Append an immutable attempt row.

        ``attempt`` shape:
          {
            "attemptId": str,
            "lessonId": str,
            "attemptedAt": str (ISO timestamp),
            "durationSec": int,
            "passed": bool,
            "score": float,
            "steps": [
              {"stepIdx": int, "conceptIds": [str], "correct": bool, "durationMs": int}
            ],
          }

        Idempotent: if an attempt with the same ``attemptId`` exists, this is a no-op.
        """
        ...

    async def list_attempts(
        self,
        user_id: str,
        lesson_id: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """List a user's attempts, newest first.

        When ``lesson_id`` is provided, returns only attempts on that lesson
        (uses main table query). When omitted, uses the ``UserAttempts-Index``
        GSI to return cross-lesson recent attempts.

        Returns (items, nextCursor).
        """
        ...

    async def get_attempts_for_concepts(
        self,
        user_id: str,
        concept_ids: list[str],
        since: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return attempt rows whose ``steps[].conceptIds`` intersects ``concept_ids``.

        Used by the lazy concept-rollup recompute path. Loads ``since`` (ISO ts)
        forward; pass ``None`` to scan the full history.
        """
        ...

    # ── Eager rollups (cheap to maintain) ───────────────────────────────────

    async def update_lesson_rollup(
        self, user_id: str, lesson_id: str, attempt: dict[str, Any]
    ) -> dict[str, Any]:
        """Update or create the per-lesson rollup atomically.

        Bumps ``attemptCount``, conditionally raises ``bestScore`` and
        ``firstPassedAt``, always updates ``latestAttemptAt``. Returns the
        new rollup state.
        """
        ...

    async def get_lesson_rollups(
        self, user_id: str
    ) -> list[dict[str, Any]]:
        """Return all per-lesson rollups for the user."""
        ...

    async def update_day_rollup(
        self,
        user_id: str,
        date: str,
        lessons_inc: int,
        minutes_inc: int,
        xp_inc: int,
    ) -> dict[str, Any]:
        """Atomically increment a day rollup. Creates the row if absent."""
        ...

    async def get_day_rollups(
        self, user_id: str, since: str, until: str
    ) -> list[dict[str, Any]]:
        """Return day rollups in the ``since..until`` (inclusive) range."""
        ...

    # ── Lazy concept rollup ────────────────────────────────────────────────

    async def invalidate_concepts(
        self, user_id: str, concept_ids: list[str], staleAt: str
    ) -> None:
        """Mark concept rollups as stale. Cheap operation — just updates
        ``staleAt`` on each row (creates the row with staleAt set if missing).
        """
        ...

    async def get_concept_rollups(
        self, user_id: str
    ) -> list[dict[str, Any]]:
        """Return all concept rollups for the user. Caller is responsible for
        recomputing any with ``staleAt != None`` via ``put_concept_rollup``.
        """
        ...

    async def put_concept_rollup(
        self, user_id: str, rollup: dict[str, Any]
    ) -> None:
        """Persist a recomputed concept rollup. Clears ``staleAt``."""
        ...
