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

    async def put_attempt(self, user_id: str, attempt: dict[str, Any]) -> None:
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

    async def attempt_exists(self, user_id: str, client_attempt_id: str) -> dict[str, Any] | None:
        """Return the existing attempt if ``client_attempt_id`` was already stored.

        The returned dict also carries ``rollupApplied`` (bool): whether
        ``update_lesson_rollup`` / ``update_day_rollup`` / ``mark_rollup_applied``
        already ran for this attempt. ``put_attempt`` and the rollup writes are
        NOT one transaction — a request can die in between (repo error, Lambda
        freeze) after the attempt row lands but before its rollups do. The
        router uses ``rollupApplied`` to tell that half-written state apart
        from a fully-processed attempt on retry, instead of treating "the
        CLIENT# row exists" as proof the rollups ran (see
        ``docs/progress-sync-contract-2026-09-17.md``). Rows written before
        this field existed have no ``rollupApplied`` attribute; both repo
        implementations default that case to ``True`` (assume already
        applied) rather than ``False``, so a legacy row is never silently
        re-rolled-up and double-counted on an old client's retry.
        """
        ...

    async def mark_rollup_applied(self, user_id: str, client_attempt_id: str) -> None:
        """Flip ``rollupApplied`` to True on the attempt's idempotency row.

        Called once, after ``update_lesson_rollup`` + ``update_day_rollup``
        both succeed for a given ``client_attempt_id``. No-op if the row is
        missing (nothing to mark).
        """
        ...

    async def update_attempt_steps(
        self,
        user_id: str,
        client_attempt_id: str,
        steps: list[dict[str, Any]],
    ) -> None:
        """Overwrite the ``steps`` column of an existing attempt row.

        Used for mid-lesson drafts (``clientAttemptId`` prefixed with
        ``draft:``) so each sync can update the cumulative step list
        rather than being IGNOREd by the idempotent put_attempt path.
        No-op if the row doesn't exist.
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

    async def update_lesson_rollup(self, user_id: str, lesson_id: str, attempt: dict[str, Any]) -> dict[str, Any]:
        """Update or create the per-lesson rollup atomically.

        Bumps ``attemptCount``, conditionally raises ``bestScore`` and
        ``firstPassedAt``, always updates ``latestAttemptAt``. Returns the
        new rollup state.
        """
        ...

    async def get_lesson_rollups(self, user_id: str) -> list[dict[str, Any]]:
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

    async def get_day_rollups(self, user_id: str, since: str, until: str) -> list[dict[str, Any]]:
        """Return day rollups in the ``since..until`` (inclusive) range."""
        ...

    # ── Lazy concept rollup ────────────────────────────────────────────────
    # TODO (ADR-0001 phase 2): the lazy recompute path never landed. The
    # ``invalidate_concepts`` plumbing below is no longer called from the hot
    # write path (Fix 11) but kept on the protocol for when phase 2 ships.

    async def invalidate_concepts(self, user_id: str, concept_ids: list[str], staleAt: str) -> None:
        """Mark concept rollups as stale. Cheap operation — just updates
        ``staleAt`` on each row (creates the row with staleAt set if missing).
        """
        ...

    async def get_concept_rollups(self, user_id: str) -> list[dict[str, Any]]:
        """Return all concept rollups for the user. Caller is responsible for
        recomputing any with ``staleAt != None`` via ``put_concept_rollup``.
        """
        ...

    async def put_concept_rollup(self, user_id: str, rollup: dict[str, Any]) -> None:
        """Persist a recomputed concept rollup. Clears ``staleAt``."""
        ...

    async def delete_all_for_user(self, user_id: str) -> None:
        """Remove all progress rows for the user (Start over / account reset)."""
        ...

    # ── Bulk-complete (2026-09-18, test-out/placement seed) ─────────────────
    # `POST /progress/lessons/bulk-complete` — a seeded completion (test-out,
    # placement) carries no attempt data (no durations, no step results), so
    # it never belonged in `ATTEMPT#`/`CLIENT#`/the per-attempt `lessons/batch`
    # path at all. This is a lighter write: one row per lesson via
    # `update_lesson_rollup`'s existing first-wins semantics (XP-neutral and
    # day-rollup-exempt by construction — this path never touches the user
    # row or `DAY#`), plus a whole-OP idempotency cache so a retried
    # `clientOpId` returns the exact prior response instead of re-deriving it.

    async def get_bulk_op(self, user_id: str, client_op_id: str) -> dict[str, Any] | None:
        """Return the cached result of a prior FULLY-SUCCEEDED bulk-complete
        call with this ``clientOpId``, or None if it hasn't run (or the prior
        run didn't fully succeed — see ``save_bulk_op``, only a complete
        success is cached)."""
        ...

    async def save_bulk_op(self, user_id: str, client_op_id: str, result: dict[str, Any]) -> None:
        """Cache a bulk-complete op's result, keyed by ``clientOpId``.

        ``result`` shape: ``{"accepted": int, "alreadyComplete": int, "total": int}``.
        Caller only calls this when every lesson id in the op resolved
        (``accepted + alreadyComplete == total``) — a partial failure is
        deliberately left uncached so a retry with the SAME `clientOpId`
        re-attempts the ids that didn't land, rather than replaying a stale
        partial result forever.
        """
        ...

    async def bulk_complete_lessons(self, user_id: str, lesson_ids: list[str], completed_at: str) -> tuple[int, int, list[str]]:
        """Mark each lesson id complete via `update_lesson_rollup` (score=1.0,
        passed=True), fanned out with per-item isolation — one id's write
        failure doesn't block the rest.

        Returns ``(accepted, already_complete, failed_ids)`` where
        ``accepted`` is the count whose `firstPassedAt` this call just set
        (a genuinely new completion) and ``already_complete`` is the count
        that already had one (this call's write was a no-op, per the
        first-wins rule `update_lesson_rollup` already enforces). Ids in
        ``failed_ids`` counted toward neither — the caller must not cache
        the op's result when this list is non-empty.
        """
        ...
