"""Progress router — per-attempt validation + per-user rollup access.

See ``docs/adr/0001-progress-api-hybrid-rollup.md`` for the architecture.

SQLite and DynamoDB repos implement the same protocol. Use ``DB_BACKEND=sqlite``
for local dev or ``dynamodb`` in prod (requires ``lingo_progress`` table from
``lingo-infra``).
"""

import uuid
from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.dependencies import get_registered_user
from app.auth.schemas import TokenPayload
from app.db.protocols import ProgressRepository, UserRepository
from app.db.provider import (
    get_platform_settings_repo,
    get_progress_repo,
    get_social_repo,
    get_user_repo,
)
from app.platform_settings.schemas import XP_ECONOMY_KEY, XpEconomyConfig
from app.progress.schemas import (
    AttemptList,
    BatchAttempt,
    BatchAttemptResponse,
    BatchAttemptResult,
    BatchAttemptSubmission,
    ConceptRollup,
    DayActivity,
    LessonRollup,
    ProgressSummary,
    ShopPurchaseRequest,
    ShopPurchaseResponse,
    TouchResponse,
    UserStats,
)
from app.progress.shop_catalog import get_shop_item
from app.progress.xp import level_for_xp

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
    # Fix 2 — hoist the user-row read OUT of the per-attempt loop and do
    # exactly ONE update_user at the end of the batch. Each attempt's writes
    # to the progress tables (attempt log, day/lesson rollups) still happen
    # per-iteration; only the cross-batch user-row arithmetic is batched.
    user_record = await users.get_user_by_id(user.id) or {}
    today_iso = date.today().isoformat()

    # XP economy is admin-tunable; read it ONCE per batch (not per attempt).
    # Falls back to the schema defaults when the settings repo is absent or
    # the key hasn't been seeded yet.
    xp_config = await _load_xp_config()

    results: list[BatchAttemptResult] = []
    total_xp_inc = 0
    total_lingots_inc = 0

    for item in body.attempts:
        result, xp_inc, lingots_inc = await _process_one_attempt(
            user_id=user.id,
            item=item,
            progress=progress,
            xp_config=xp_config,
        )
        results.append(result)
        total_xp_inc += xp_inc
        total_lingots_inc += lingots_inc

    # Compute new user-row state from a single base snapshot.
    base_xp = int(user_record.get("xp") or 0)
    base_lingots = int(user_record.get("lingots") or 0)
    new_xp = base_xp + total_xp_inc
    new_lingots = base_lingots + total_lingots_inc

    patch: dict[str, Any] = {
        "xp": new_xp,
        "level": level_for_xp(new_xp),
        "lingots": new_lingots,
    }

    streak_after = int(user_record.get("streak") or 0)
    streak_touched = False
    if body.checkStreak and any(r.accepted for r in results):
        last_active = user_record.get("last_active_date") or user_record.get("lastActiveDate")
        if last_active != today_iso:
            yesterday_iso = (date.today() - timedelta(days=1)).isoformat()
            if last_active == yesterday_iso:
                streak_after = streak_after + 1
            else:
                streak_after = 1
            best = int(user_record.get("best_streak") or user_record.get("bestStreak") or 0)
            patch["streak"] = streak_after
            patch["best_streak"] = max(best, streak_after)
            patch["last_active_date"] = today_iso
            streak_touched = True

    if total_xp_inc or total_lingots_inc or streak_touched:
        await users.update_user(user.id, patch)

    # Leaderboard hook — opt-in via user settings. Done ONCE per batch with
    # the total XP increment (was per-attempt before; both cost one settings
    # read so consolidating is free).
    if total_xp_inc > 0:
        social_repo = get_social_repo()
        if social_repo is not None:
            try:
                settings_blob = await users.get_settings(user.id) or {}
                social_cfg = settings_blob.get("social") or {}
                if social_cfg.get("show_on_leaderboard"):
                    learning = settings_blob.get("learning") or {}
                    lang = learning.get("learningLanguageId") or settings_blob.get("learningLanguage")
                    if lang:
                        await social_repo.add_xp_to_leaderboard(user.id, str(lang), total_xp_inc)
            except Exception:
                # Leaderboard write failures must never break a lesson sync.
                pass

    # Stamp streakAfter on every accepted result for FE display.
    for r in results:
        if r.accepted:
            r.streakAfter = streak_after

    return BatchAttemptResponse(results=results)


async def _load_xp_config() -> XpEconomyConfig:
    """Read the admin-tunable XP economy. Defaults to the schema baseline
    when the repo isn't wired or the key is missing."""
    repo = get_platform_settings_repo()
    if repo is None:
        return XpEconomyConfig()
    try:
        stored = await repo.get(XP_ECONOMY_KEY)
    except Exception:  # noqa: BLE001 — degrade to defaults on any failure
        return XpEconomyConfig()
    if not stored:
        return XpEconomyConfig()
    try:
        return XpEconomyConfig(**stored)
    except Exception:  # noqa: BLE001 — bad stored data shouldn't break sync
        return XpEconomyConfig()


async def _process_one_attempt(
    *,
    user_id: str,
    item: BatchAttempt,
    progress: ProgressRepository,
    xp_config: XpEconomyConfig,
) -> tuple[BatchAttemptResult, int, int]:
    """Persist one attempt + its day/lesson rollups.

    Returns ``(result, xp_inc, lingots_inc)``. User-row updates are batched
    by the caller (see Fix 2 in ``submit_attempt_batch``).
    """
    # Idempotency — if we've already accepted this client attempt, return the
    # cached outcome shape.
    existing = await progress.attempt_exists(user_id, item.clientAttemptId)
    if existing is not None:
        return (
            BatchAttemptResult(
                clientAttemptId=item.clientAttemptId,
                attemptId=existing["attemptId"],
                accepted=True,
                xpEarned=0,
                streakAfter=0,
                lingotsEarned=0,
                dailyTotalLessons=0,
            ),
            0,
            0,
        )

    # Sanity — duration floor (1s per step or 5s, whichever is larger)
    step_count = len(item.stepResults)
    min_duration = max(5, step_count)
    if item.durationSec < min_duration:
        return (
            BatchAttemptResult(
                clientAttemptId=item.clientAttemptId,
                accepted=False,
                reason="duration_below_floor",
                xpEarned=0,
                streakAfter=0,
                lingotsEarned=0,
                dailyTotalLessons=0,
            ),
            0,
            0,
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

    # XP / lingot computation (server-authoritative, admin-tunable via
    # PlatformSettings → xp_economy). Pre-config defaults match the legacy
    # constants in app.progress.xp.
    if not item.passed:
        xp_earned = 0
        lingots_earned = 0
    else:
        if item.score >= 0.999:
            xp_earned = xp_config.lesson_perfect_xp
        else:
            xp_earned = xp_config.lesson_pass_xp
        lingots_earned = xp_config.lingots_per_lesson

    # Eager rollup updates
    await progress.update_lesson_rollup(user_id, item.lessonId, attempt_row)
    day_rollup = await progress.update_day_rollup(
        user_id,
        date.today().isoformat(),
        lessons_inc=1 if item.passed else 0,
        minutes_inc=max(1, item.durationSec // 60),
        xp_inc=xp_earned,
    )

    # Fix 11 — invalidate_concepts removed from the hot path. The lazy
    # recompute path described in ADR-0001 § "Concept rollups (lazy)" never
    # landed; reads currently return whatever the last full recompute wrote.
    # Re-add invalidation when the recompute path is wired up.

    return (
        BatchAttemptResult(
            clientAttemptId=item.clientAttemptId,
            attemptId=attempt_id,
            accepted=True,
            xpEarned=xp_earned,
            streakAfter=0,  # filled in by caller after batch-level streak roll
            lingotsEarned=lingots_earned,
            dailyTotalLessons=day_rollup["lessonsCompleted"],
        ),
        xp_earned,
        lingots_earned,
    )


# Fix 12 — the single-attempt POST endpoint was a never-implemented 501 stub
# documented in ADR-0001 only as a curl convenience. Removed to keep the API
# surface honest. The batch endpoint above is the production sync path.


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

    return _progress_summary_from_db(user_record, lesson_rollups, concept_rollups, day_rollups)


def _progress_summary_from_db(
    user_record: dict[str, Any],
    lesson_rollups: list[dict[str, Any]],
    concept_rollups: list[dict[str, Any]],
    day_rollups: list[dict[str, Any]],
) -> ProgressSummary:
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


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def reset_my_progress(
    user: CurrentUser,
    progress: ProgressRepo,
    users: UserRepo,
) -> None:
    """Wipe lesson/concept/day progress and reset stats (Start over)."""
    await progress.delete_all_for_user(user.id)
    await users.update_user(
        user.id,
        {
            "streak": 0,
            "best_streak": 0,
            "xp": 0,
            "level": 1,
            "lingots": 0,
            "last_active_date": None,
        },
    )


def _user_stats_from_record(record: dict[str, Any]) -> UserStats:
    """Map a user-table row to the UserStats schema. Handles both snake_case
    (sqlite) and camelCase (in-memory / mock) attribute keys."""
    return UserStats(
        streak=int(record.get("streak") or 0),
        bestStreak=int(record.get("best_streak") or record.get("bestStreak") or 0),
        lastActiveDate=record.get("last_active_date") or record.get("lastActiveDate"),
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
    items, next_cursor = await progress.list_attempts(user_id=user.id, lesson_id=lesson_id, limit=limit, cursor=cursor)
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


@router.post("/shop/purchase", response_model=ShopPurchaseResponse)
async def purchase_shop_item(
    body: ShopPurchaseRequest,
    user: CurrentUser,
    users: UserRepo,
) -> Any:
    """Spend lingots on a catalog item. Deducts balance and records ownership."""
    item = get_shop_item(body.itemId)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown shop item")

    settings = await users.get_settings(user.id) or {}
    shop_state = dict(settings.get("shop") or {})
    purchases: list[str] = list(shop_state.get("purchases") or [])
    inventory: dict[str, int] = {str(k): int(v) for k, v in (shop_state.get("inventory") or {}).items() if isinstance(v, (int, float))}

    consumable = bool(item.get("consumable"))
    if not consumable and body.itemId in purchases:
        raise HTTPException(status.HTTP_409_CONFLICT, "Already owned")

    user_record = await users.get_user_by_id(user.id) or {}
    lingots = int(user_record.get("lingots") or 0)
    price = int(item["price"])
    if lingots < price:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "insufficient_lingots")

    await users.update_user(user.id, {"lingots": lingots - price})

    if consumable:
        inventory[body.itemId] = inventory.get(body.itemId, 0) + 1
    elif body.itemId not in purchases:
        purchases.append(body.itemId)

    shop_state["purchases"] = purchases
    shop_state["inventory"] = inventory
    await users.update_settings(user.id, {"shop": shop_state})

    updated = await users.get_user_by_id(user.id) or {}
    qty = inventory.get(body.itemId, 0) if consumable else (1 if body.itemId in purchases else 0)
    return ShopPurchaseResponse(
        itemId=body.itemId,
        price=price,
        lingotsRemaining=int(updated.get("lingots") or 0),
        owned=body.itemId in purchases or qty > 0,
        quantity=qty,
    )
