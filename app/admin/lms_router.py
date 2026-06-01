"""Admin LMS (Learning Management System) endpoints.

Surface for moderators to inspect and edit a user's learning state:
  - GET    /admin/lms/{user_id}            — full LMS snapshot
  - PATCH  /admin/lms/{user_id}/learning   — set currentLesson / currentModule / learningLanguageId
  - POST   /admin/lms/{user_id}/xp         — award / retract XP
  - DELETE /admin/lms/{user_id}/progress   — wipe lesson+day rollups, reset XP/streak

All routes require admin role (via require_admin dependency).
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.admin.audit_router import record_admin_action
from app.auth.dependencies import require_admin
from app.auth.schemas import TokenPayload
from app.db.protocols import ProgressRepository, UserRepository
from app.db.provider import get_progress_repo, get_user_repo
from app.progress.xp import level_for_xp
from app.shared.errors import api_error
from app.shared.repos import require_repo

router = APIRouter(tags=["admin", "lms"])

AdminUser = Annotated[TokenPayload, Depends(require_admin)]
UserRepo = Annotated[UserRepository, Depends(get_user_repo)]
ProgressRepo = Annotated[ProgressRepository | None, Depends(get_progress_repo)]


# ── Schemas ──────────────────────────────────────────────────────────────────


class LmsLearningState(BaseModel):
    """The learning sub-blob from user_settings."""

    learningLanguageId: str | None = None
    currentModule: str | None = None
    currentLesson: str | None = None


class LmsUserStats(BaseModel):
    xp: int = 0
    level: int = 1
    streak: int = 0
    bestStreak: int = 0
    lingots: int = 0
    lastActiveDate: str | None = None


class LmsLessonSummary(BaseModel):
    lessonId: str
    bestScore: float
    firstPassedAt: str | None
    latestAttemptAt: str
    attemptCount: int


class LmsSnapshot(BaseModel):
    """Full LMS view of a user — all learning data in one payload."""

    userId: str
    username: str
    displayName: str
    learning: LmsLearningState
    stats: LmsUserStats
    completedLessons: list[LmsLessonSummary]


class LmsLearningPatch(BaseModel):
    """Partial update for the learning sub-blob. Only supplied keys are changed."""

    learningLanguageId: str | None = Field(default=None, max_length=16)
    currentModule: str | None = Field(default=None, max_length=64)
    currentLesson: str | None = Field(default=None, max_length=128)


class LmsXpAward(BaseModel):
    """XP adjustment. Positive = grant, negative = retract (floor 0)."""

    amount: int = Field(..., ge=-1_000_000, le=1_000_000)
    reason: str = Field(default="admin-lms", max_length=500)


class LmsXpResponse(BaseModel):
    userId: str
    xp: int
    level: int
    awarded: int


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/{user_id}", response_model=LmsSnapshot)
async def get_lms_snapshot(
    user_id: str,
    _admin: AdminUser,
    users: UserRepo,
    progress: ProgressRepo,
) -> Any:
    """Return the full LMS snapshot for a user."""
    r = require_repo(users, "user")
    with api_error("fetching LMS snapshot"):
        record = await r.get_user_by_id(user_id)
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
        settings = await r.get_settings(user_id) or {}
        learning_blob = settings.get("learning") or {}

        lesson_rollups: list[dict[str, Any]] = []
        if progress is not None:
            lesson_rollups = await progress.get_lesson_rollups(user_id)

    return LmsSnapshot(
        userId=user_id,
        username=record.get("username", ""),
        displayName=record.get("display_name", ""),
        learning=LmsLearningState(
            learningLanguageId=learning_blob.get("learningLanguageId"),
            currentModule=learning_blob.get("currentModule"),
            currentLesson=learning_blob.get("currentLesson"),
        ),
        stats=LmsUserStats(
            xp=int(record.get("xp") or 0),
            level=int(record.get("level") or 1),
            streak=int(record.get("streak") or 0),
            bestStreak=int(record.get("best_streak") or record.get("bestStreak") or 0),
            lingots=int(record.get("lingots") or 0),
            lastActiveDate=record.get("last_active_date") or record.get("lastActiveDate"),
        ),
        completedLessons=[
            LmsLessonSummary(
                lessonId=rollup["lessonId"],
                bestScore=float(rollup.get("bestScore") or 0),
                firstPassedAt=rollup.get("firstPassedAt"),
                latestAttemptAt=rollup.get("latestAttemptAt", ""),
                attemptCount=int(rollup.get("attemptCount") or 0),
            )
            for rollup in lesson_rollups
            if rollup.get("firstPassedAt")  # only show lessons the user actually passed
        ],
    )


@router.patch("/{user_id}/learning", response_model=LmsLearningState)
async def patch_lms_learning(
    user_id: str,
    body: LmsLearningPatch,
    _admin: AdminUser,
    users: UserRepo,
) -> Any:
    """Patch the user's learning sub-blob (currentLesson, currentModule, learningLanguageId)."""
    r = require_repo(users, "user")
    with api_error("patching LMS learning state"):
        record = await r.get_user_by_id(user_id)
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

        settings = await r.get_settings(user_id) or {}
        learning_blob = dict(settings.get("learning") or {})

        patch_data = body.model_dump(exclude_none=True)
        if not patch_data:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty patch body")

        learning_blob.update(patch_data)
        await r.update_settings(user_id, {"learning": learning_blob})

    await record_admin_action(
        actor_id=_admin.id,
        action="lms_patch_learning",
        target_id=user_id,
        target_kind="user",
        payload=patch_data,
    )
    return LmsLearningState(**learning_blob)


@router.post("/{user_id}/xp", response_model=LmsXpResponse)
async def award_lms_xp(
    user_id: str,
    body: LmsXpAward,
    _admin: AdminUser,
    users: UserRepo,
    progress: ProgressRepo,
) -> Any:
    """Award or retract XP for a user via the LMS surface."""
    if body.amount == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "amount must be non-zero")
    r = require_repo(users, "user")

    with api_error("awarding LMS XP"):
        record = await r.get_user_by_id(user_id)
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

        base_xp = int(record.get("xp") or 0)
        new_xp = max(0, base_xp + body.amount)
        new_level = level_for_xp(new_xp)
        await r.update_user(user_id, {"xp": new_xp, "level": new_level})

        if body.amount > 0 and progress is not None:
            from datetime import UTC, datetime

            today_iso = datetime.now(UTC).date().isoformat()
            try:
                await progress.update_day_rollup(
                    user_id, today_iso, lessons_inc=0, minutes_inc=0, xp_inc=body.amount
                )
            except Exception:  # noqa: BLE001 — day-rollup failure must never break the XP write
                pass

    await record_admin_action(
        actor_id=_admin.id,
        action="lms_award_xp",
        target_id=user_id,
        target_kind="user",
        payload={"amount": body.amount, "reason": body.reason, "new_xp": new_xp},
    )
    return LmsXpResponse(userId=user_id, xp=new_xp, level=new_level, awarded=body.amount)


@router.delete("/{user_id}/progress", status_code=status.HTTP_204_NO_CONTENT)
async def reset_lms_progress(
    user_id: str,
    _admin: AdminUser,
    users: UserRepo,
    progress: ProgressRepo,
) -> None:
    """Wipe all lesson/day rollups and reset XP/streak for a user (destructive)."""
    r = require_repo(users, "user")

    with api_error("resetting LMS progress"):
        record = await r.get_user_by_id(user_id)
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
        if progress is not None:
            await progress.delete_all_for_user(user_id)
        await r.update_user(
            user_id,
            {
                "xp": 0,
                "level": 1,
                "lingots": 0,
                "streak": 0,
                "best_streak": 0,
                "last_active_date": None,
            },
        )

    await record_admin_action(
        actor_id=_admin.id,
        action="lms_reset_progress",
        target_id=user_id,
        target_kind="user",
        payload={},
    )
