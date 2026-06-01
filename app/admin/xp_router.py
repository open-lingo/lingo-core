"""Admin "Award XP" endpoint — manual XP grant for testing leaderboards.

Splits out of ``app/admin/router.py`` so the surface stays scannable; the
prefix mount in ``app/v1/router.py`` is the only wiring required.

The endpoint reads/writes the target user's row via the same code path as
``app/progress/router.py`` (no atomic-increment in the repo yet — read,
add, write). It also writes to ``progress_day_rollups`` so the awarded XP
appears in the leaderboard (which aggregates XP from that table over a
rolling window). The social repo leaderboard mirror is kept best-effort for
the DynamoDB prod path but must never block the response.
"""

from datetime import UTC, datetime
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

router = APIRouter(tags=["admin", "xp"])

AdminUser = Annotated[TokenPayload, Depends(require_admin)]
UserRepo = Annotated[UserRepository, Depends(get_user_repo)]
ProgressRepo = Annotated[ProgressRepository | None, Depends(get_progress_repo)]


class AwardXpRequest(BaseModel):
    """Manual XP grant. ``amount`` may be negative (clamps the user's XP
    to zero) so admins can also undo bad awards."""

    amount: int = Field(..., ge=-1_000_000, le=1_000_000)
    reason: str = Field(default="", max_length=500)


class AwardXpResponse(BaseModel):
    user_id: str
    xp: int
    level: int
    awarded: int
    reason: str


@router.post(
    "/users/{user_id}/award-xp",
    response_model=AwardXpResponse,
)
async def award_xp(
    user_id: str,
    body: AwardXpRequest,
    _admin: AdminUser,
    users: UserRepo,
    progress: ProgressRepo,
) -> Any:
    """Grant ``amount`` XP to ``user_id``. Updates the user row, writes the
    day rollup so the leaderboard reflects the award immediately, and mirrors
    to the social-repo leaderboard best-effort for the DynamoDB prod path."""
    if body.amount == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "amount must be non-zero")

    with api_error("awarding xp"):
        record = await users.get_user_by_id(user_id)
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

        base_xp = int(record.get("xp") or 0)
        new_xp = max(0, base_xp + body.amount)
        new_level = level_for_xp(new_xp)

        # Single write — read-modify-write is fine here because admin
        # award is not a hot path and the user-row is not contended.
        await users.update_user(user_id, {"xp": new_xp, "level": new_level})

        # Write the day rollup so the leaderboard (which reads
        # progress_day_rollups) reflects admin-granted XP in the rolling
        # window. Only write positive amounts; negative grants (rollbacks)
        # do not reverse historical rollup rows — they just shrink user.xp.
        if body.amount > 0 and progress is not None:
            try:
                today_iso = datetime.now(UTC).date().isoformat()
                await progress.update_day_rollup(
                    user_id, today_iso, lessons_inc=0, minutes_inc=0, xp_inc=body.amount
                )
            except Exception:  # noqa: BLE001 — must never break the admin write
                pass

    await record_admin_action(
        actor_id=_admin.id,
        action="award_xp",
        target_id=user_id,
        target_kind="user",
        payload={"amount": body.amount, "reason": body.reason, "new_xp": new_xp},
    )
    return AwardXpResponse(
        user_id=user_id,
        xp=new_xp,
        level=new_level,
        awarded=body.amount,
        reason=body.reason,
    )


