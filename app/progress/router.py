"""Progress router — per-attempt validation + per-user rollup access.

See ``docs/adr/0001-progress-api-hybrid-rollup.md`` for the architecture.

Concrete repository implementations (SQLite + Dynamo) are not yet wired —
the dependency below will raise at request time until ``init_repositories``
constructs a ``ProgressRepository``. That's intentional: this router lands
the contract and endpoint shape so the frontend can start coding against
it, while the storage layer is a separate follow-up.
"""

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.dependencies import get_registered_user
from app.auth.schemas import TokenPayload
from app.db.protocols import ProgressRepository, UserRepository
from app.db.provider import get_user_repo  # progress provider TBD
from app.progress.schemas import (
    AttemptList,
    AttemptResponse,
    AttemptSubmission,
    BatchAttemptResponse,
    BatchAttemptResult,
    BatchAttemptSubmission,
    ConceptDelta,
    ProgressSummary,
    StepResult,
    TouchResponse,
    UserStats,
)
from app.progress.xp import (
    lingots_for_attempt,
    level_for_xp,
    xp_for_attempt,
)

router = APIRouter(tags=["progress"])

CurrentUser = Annotated[TokenPayload, Depends(get_registered_user)]
UserRepo = Annotated[UserRepository, Depends(get_user_repo)]


def _get_progress_repo() -> ProgressRepository:
    """Placeholder dependency. Once init_repositories provides a concrete
    ProgressRepository, swap this for the equivalent of get_srs_repo()."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Progress repository not yet wired — see ADR-0001 follow-up",
    )


ProgressRepo = Annotated[ProgressRepository, Depends(_get_progress_repo)]


# ── Submission ──────────────────────────────────────────────────────────────


@router.post(
    "/lessons/batch",
    response_model=BatchAttemptResponse,
)
async def submit_attempt_batch(
    body: BatchAttemptSubmission,
    user: CurrentUser,
    progress: ProgressRepo,
    users: UserRepo,
) -> Any:
    """Batch sync endpoint — the main client sync path.

    The frontend buffers lesson completions in localStorage and flushes the
    whole buffer here via the SyncManager (same UX as SRS sync). One write
    transaction per attempt is fine because each item is small; cost is
    dominated by the SyncManager flush cadence (manual + periodic + on exit)
    rather than per-completion event.

    See ADR-0001 § "Sync model — batch, not per-event" for rationale.

    Each item is processed in order:
      1. Idempotency on (userId, clientAttemptId) — re-pushing returns prior result
      2. Sanity (durationSec floor/ceiling)
      3. Prerequisite check (previous lesson in module has ≥1 attempt)
      4. Persist attempt + rollup updates (per the hybrid flow)
      5. Return per-attempt result (or rejection reason)

    Phase 1: server trusts the client-graded `stepResults`. Phase 2: server
    re-validates against its own answer store before persisting.

    Streak update:
      The body's ``checkStreak`` flag is client-driven. When true (first sync
      of a new local day), this handler runs the streak GetItem + conditional
      UpdateItem on the user row exactly once for this batch. When false
      (default, every subsequent same-day sync), the handler skips the streak
      path entirely. The XP / lingots / day-rollup writes still happen per
      attempt regardless. See ADR-0001 § "Streak check: client-driven, not
      per-attempt" for the contract.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Batch progress sync pending repo wiring",
    )


@router.post(
    "/lessons/{lesson_id}/attempt",
    response_model=AttemptResponse,
)
async def submit_attempt(
    lesson_id: str,
    body: AttemptSubmission,
    user: CurrentUser,
    progress: ProgressRepo,
    users: UserRepo,
) -> Any:
    """Validate a lesson attempt server-side, write attempt + rollups, return result.

    Per-attempt write flow (non-transactional, see ADR-0001):

      1. Load server-side answer key for ``lesson_id`` from curriculum store
      2. Validate each step against the key, compute pass/fail and score
      3. Sanity checks: durationSec floor/ceiling, prerequisite met
      4. PutItem ATTEMPT row (idempotent on clientAttemptId)
      5. UpdateItem user row (ADD xp, ADD lingots, conditional streak update)
      6. UpdateItem LESSON rollup (attemptCount, bestScore, latestAttemptAt)
      7. UpdateItem DAY rollup (lessonsCompleted, minutesActive, xpEarned)
      8. Invalidate CONCEPT rollups touched by this attempt (set staleAt = now)
      9. Return result with conceptDeltas computed from the in-memory state
    """
    # Sanity: durationSec floor/ceiling are enforced by the schema (Field ge/le)
    if body.durationSec < max(5, len(body.answers) * 1):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="durationSec below minimum for step count",
        )

    # TODO: load answer key from app/curriculum/answers/<lesson_id>.json
    # TODO: validate steps
    # TODO: write attempt + rollups
    # TODO: compute conceptDeltas

    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Progress validation pipeline pending — answer-key extraction script + repo wiring",
    )


# ── Reads ───────────────────────────────────────────────────────────────────


@router.get("/me", response_model=ProgressSummary)
async def get_my_progress(
    user: CurrentUser,
    progress: ProgressRepo,
    users: UserRepo,
) -> Any:
    """One-shot aggregate for page render.

    Reads (in parallel where the storage layer supports it):
      - User row → UserStats
      - Lesson rollups via main-table query (SK begins_with LESSON#)
      - Concept rollups via main-table query (SK begins_with CONCEPT#)
        Any with staleAt != None are recomputed lazily from the attempt log.
      - Last 30 days of DAY# rollups
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Progress read pipeline pending",
    )


@router.get("/me/attempts", response_model=AttemptList)
async def list_my_attempts(
    user: CurrentUser,
    progress: ProgressRepo,
    lesson_id: str | None = Query(None, alias="lessonId"),
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = None,
) -> Any:
    """Paginated attempt history.

    - With ``lessonId``: main-table query, sorted newest first by SK suffix
    - Without ``lessonId``: ``UserAttempts-Index`` GSI query, sorted by ``attemptedAt`` desc
    """
    items, next_cursor = await progress.list_attempts(
        user_id=user.id, lesson_id=lesson_id, limit=limit, cursor=cursor
    )
    return AttemptList(
        items=[
            {
                "attemptId": item["attemptId"],
                "lessonId": item["lessonId"],
                "attemptedAt": item["attemptedAt"],
                "durationSec": int(item["durationSec"]),
                "passed": bool(item["passed"]),
                "score": float(item["score"]),
            }
            for item in items
        ],
        nextCursor=next_cursor,
    )


@router.post("/me/touch", response_model=TouchResponse)
async def touch_session(
    user: CurrentUser,
    progress: ProgressRepo,
    users: UserRepo,
) -> Any:
    """Lightweight session-start hook.

    The frontend calls this once after Auth0 token acquisition to:
      - Refresh the streak (today's first activity ticks the streak)
      - Surface which concept rollups went stale since last login (the
        client can prefetch them, or the next /me read does the recompute)

    Cheap: single user-row update + a concept SK begins_with query that
    only returns rows with staleAt != None.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Touch endpoint pending repo wiring",
    )


# ── Helpers ─────────────────────────────────────────────────────────────────


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _today_yyyymmdd() -> str:
    return datetime.now(UTC).date().isoformat()
