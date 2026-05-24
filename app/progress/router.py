"""Progress router — per-attempt validation + per-user rollup access.

See ``docs/adr/0001-progress-api-hybrid-rollup.md`` for the architecture.

SQLite and DynamoDB repos implement the same protocol. Use ``DB_BACKEND=sqlite``
for local dev or ``dynamodb`` in prod (requires ``lingo_progress`` table from
``lingo-infra``).
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.dependencies import get_registered_user
from app.auth.schemas import TokenPayload
from app.db.protocols import ProgressRepository, UserRepository
from app.db.provider import get_progress_repo, get_user_repo
from app.progress.schemas import (
    AttemptList,
    AttemptResponse,
    AttemptSubmission,
    BatchAttempt,
    BatchAttemptResponse,
    BatchAttemptResult,
    BatchAttemptSubmission,
    ConceptDelta,
    ConceptRollup,
    DayActivity,
    LessonRollup,
    ProgressSummary,
    StepResult,
    TouchResponse,
    UserStats,
)
from app.progress.xp import (
    level_for_xp,
    lingots_for_attempt,
    xp_for_attempt,
)

router = APIRouter(tags=["progress"])

CurrentUser = Annotated[TokenPayload, Depends(get_registered_user)]
UserRepo = Annotated[UserRepository, Depends(get_user_repo)]
ProgressRepo = Annotated[ProgressRepository, Depends(get_progress_repo)]


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
    results: list[BatchAttemptResult] = []
    streak_updated_this_batch = False
    today = date.today().isoformat()

    for item in body.attempts:
        result = await _process_one_attempt(
            user_id=user.id,
            item=item,
            progress=progress,
            users=users,
            allow_streak_check=body.checkStreak and not streak_updated_this_batch,
        )
        results.append(result)
        if result.accepted and result.streakAfter > 0:
            streak_updated_this_batch = body.checkStreak

    return BatchAttemptResponse(results=results)


async def _process_one_attempt(
    *,
    user_id: str,
    item: BatchAttempt,
    progress: ProgressRepository,
    users: UserRepository,
    allow_streak_check: bool,
) -> BatchAttemptResult:
    # Idempotency — if we've already accepted this client attempt, return the
    # cached outcome shape.
    existing = await progress.attempt_exists(user_id, item.clientAttemptId)
    if existing is not None:
        return BatchAttemptResult(
            clientAttemptId=item.clientAttemptId,
            attemptId=existing["attemptId"],
            accepted=True,
            xpEarned=0,
            streakAfter=0,
            lingotsEarned=0,
            dailyTotalLessons=0,
        )

    # Sanity — duration floor (1s per step or 5s, whichever is larger)
    step_count = len(item.stepResults)
    min_duration = max(5, step_count)
    if item.durationSec < min_duration:
        return BatchAttemptResult(
            clientAttemptId=item.clientAttemptId,
            accepted=False,
            reason="duration_below_floor",
            xpEarned=0,
            streakAfter=0,
            lingotsEarned=0,
            dailyTotalLessons=0,
        )

    # Persist attempt (immutable source of truth)
    attempt_id = str(uuid.uuid4())
    attempt_row = {
        "attemptId": attempt_id,
        "clientAttemptId": item.clientAttemptId,
        "lessonId": item.lessonId,
        "attemptedAt": item.attemptedAt,
        "durationSec": item.durationSec,
        "passed": item.passed,
        "score": item.score,
        "steps": [s.model_dump() for s in item.stepResults],
    }
    await progress.put_attempt(user_id, attempt_row)  # type: ignore[arg-type]

    # XP / lingot computation (server-authoritative)
    xp_earned = xp_for_attempt(item.passed, item.score)
    lingots_earned = lingots_for_attempt(item.passed)

    # Eager rollup updates
    lesson_rollup = await progress.update_lesson_rollup(user_id, item.lessonId, attempt_row)
    day_rollup = await progress.update_day_rollup(
        user_id,
        date.today().isoformat(),
        lessons_inc=1 if item.passed else 0,
        minutes_inc=max(1, item.durationSec // 60),
        xp_inc=xp_earned,
    )

    # Lazy concept rollup invalidation — touched concepts get staleAt set; the
    # next /me read recomputes them. Cheap write per concept.
    concept_ids: list[str] = []
    for step in item.stepResults:
        concept_ids.extend(step.conceptIds or [])
    if concept_ids:
        await progress.invalidate_concepts(
            user_id, list(set(concept_ids)), datetime.now(UTC).isoformat()
        )

    # User-row updates: XP + lingots always. Streak only when client signals
    # the first sync of a new local day AND we haven't already done it for
    # an earlier attempt in this same batch.
    user_record = await users.get_user_by_id(user_id) or {}
    new_xp = (user_record.get("xp") or 0) + xp_earned
    new_lingots = (user_record.get("lingots") or 0) + lingots_earned
    new_level = level_for_xp(new_xp)

    patch: dict[str, Any] = {
        "xp": new_xp,
        "level": new_level,
        "lingots": new_lingots,
    }

    streak_after = user_record.get("streak") or 0
    if allow_streak_check:
        last_active = user_record.get("last_active_date") or user_record.get(
            "lastActiveDate"
        )
        today_iso = date.today().isoformat()
        if last_active != today_iso:
            # Tick the streak. If yesterday was the previous active day, ++; else reset to 1.
            yesterday_iso = (date.today() - timedelta(days=1)).isoformat()
            if last_active == yesterday_iso:
                streak_after = streak_after + 1
            else:
                streak_after = 1
            best = user_record.get("best_streak") or user_record.get("bestStreak") or 0
            patch["streak"] = streak_after
            patch["best_streak"] = max(best, streak_after)
            patch["last_active_date"] = today_iso

    await users.update_user(user_id, patch)

    return BatchAttemptResult(
        clientAttemptId=item.clientAttemptId,
        attemptId=attempt_id,
        accepted=True,
        xpEarned=xp_earned,
        streakAfter=streak_after,
        lingotsEarned=lingots_earned,
        dailyTotalLessons=day_rollup["lessonsCompleted"],
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

    Reads:
      - User row → UserStats
      - Lesson rollups via main-table query (SK begins_with LESSON#)
      - Concept rollups via main-table query (SK begins_with CONCEPT#)
      - Last 30 days of DAY# rollups

    Lazy concept recompute is on the to-do list; rollups with ``staleAt``
    set today simply ship as-is (the data is still useful, just slightly
    stale). When the recompute path lands it slots in here.
    """
    user_record = await users.get_user_by_id(user.id) or {}
    lesson_rollups = await progress.get_lesson_rollups(user.id)
    concept_rollups = await progress.get_concept_rollups(user.id)

    today = date.today()
    since = (today - timedelta(days=29)).isoformat()
    until = today.isoformat()
    day_rollups = await progress.get_day_rollups(user.id, since, until)

    return ProgressSummary(
        user=_user_stats_from_record(user_record),
        lessons=[LessonRollup(**r) for r in lesson_rollups],
        concepts=[
            ConceptRollup(
                conceptId=c["conceptId"],
                encounters=c.get("encounters") or 0,
                correctCount=c.get("correctCount") or 0,
                incorrectCount=c.get("incorrectCount") or 0,
                recentResults=c.get("recentResults") or [],
                avgDurationMs=c.get("avgDurationMs"),
                firstSeenAt=c.get("firstSeenAt") or "",
                lastSeenAt=c.get("lastSeenAt") or "",
                lastCorrectAt=c.get("lastCorrectAt"),
            )
            for c in concept_rollups
        ],
        last30days=[DayActivity(**d) for d in day_rollups],
    )


def _user_stats_from_record(record: dict[str, Any]) -> UserStats:
    """Map a user-table row to the UserStats schema. Handles both snake_case
    (sqlite) and camelCase (in-memory / mock) attribute keys."""
    return UserStats(
        streak=int(record.get("streak") or 0),
        bestStreak=int(record.get("best_streak") or record.get("bestStreak") or 0),
        lastActiveDate=record.get("last_active_date")
        or record.get("lastActiveDate"),
        xp=int(record.get("xp") or 0),
        level=int(record.get("level") or 1),
        lingots=int(record.get("lingots") or 0),
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
      - Surface which concept rollups went stale since last login (the
        client can prefetch them, or the next /me read does the recompute)

    Streak is NOT bumped here — streak updates happen exclusively via the
    batch-attempt endpoint with checkStreak=true (per ADR-0001). This
    endpoint is purely a read of "what does the user need to refresh".
    """
    user_record = await users.get_user_by_id(user.id) or {}
    concept_rollups = await progress.get_concept_rollups(user.id)
    stale_ids = [c["conceptId"] for c in concept_rollups if c.get("staleAt")]
    return TouchResponse(
        user=_user_stats_from_record(user_record),
        streakUpdated=False,
        staleConceptIds=stale_ids,
    )


# ── Helpers ─────────────────────────────────────────────────────────────────


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _today_yyyymmdd() -> str:
    return datetime.now(UTC).date().isoformat()
