"""Quests API — daily/weekly/random/friend goals with progress + rewards.

The frontend has ``src/features/quests/`` rendering against ``buildMockQuestCatalog``;
this router persists the same shape server-side. Rewards (lingots + XP) are
applied to the user row on claim. Ad-free minutes + streak shields are tracked
on the quest row's ``reward_granted`` flag until those subsystems land —
documented gap, see /docs/.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth.dependencies import get_acting_user, require_internal_service
from app.auth.schemas import TokenPayload
from app.db.protocols import QuestRepository, UserRepository
from app.db.provider import get_quest_repo, get_user_repo
from app.quests.schemas import (
    Quest,
    QuestClaimResponse,
    QuestListResponse,
    QuestProgress,
    QuestProgressBody,
    QuestRefreshResponse,
    QuestRewards,
)
from app.shared.errors import api_error
from app.shared.repos import require_repo

logger = logging.getLogger("lingo.quests")

router = APIRouter(tags=["quests"])

# Honors admin impersonation so quest progress is read/written for the
# impersonated user.
CurrentUser = Annotated[TokenPayload, Depends(get_acting_user)]
QuestRepo = Annotated[QuestRepository | None, Depends(get_quest_repo)]
UserRepo = Annotated[UserRepository, Depends(get_user_repo)]


class InternalProgressBody(BaseModel):
    user_id: str
    delta: int


# ─── Helpers ─────────────────────────────────────────────────────────────────


_MS_PER_HOUR = 3_600_000
_MS_PER_DAY = 24 * _MS_PER_HOUR


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _expires_to_ms(expires_at: str | None) -> int | None:
    """Convert ISO-8601 expires_at to Unix epoch ms for the frontend."""
    if not expires_at:
        return None
    try:
        # Accept ``+00:00`` and ``Z`` suffix.
        normalized = expires_at.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return int(dt.timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def _row_to_quest(row: dict[str, Any]) -> Quest:
    rewards = QuestRewards(
        lingots=row.get("reward_lingots") or None,
        xp=row.get("reward_xp") or None,
        ad_free_minutes=row.get("reward_ad_free_minutes") or None,
        streak_shield=row.get("reward_streak_shield") or None,
    )
    progress = QuestProgress(
        current=int(row.get("progress_current") or 0),
        target=int(row.get("progress_target") or 0),
        unit=row.get("progress_unit") or "",
    )
    status_str = row.get("status") or "active"
    expires_at_ms = _expires_to_ms(row.get("expires_at"))
    if status_str not in ("completed",) and expires_at_ms is not None and expires_at_ms < int(datetime.now(UTC).timestamp() * 1000):
        status_str = "expired"
    return Quest(
        id=row["id"],
        type=row["type"],
        title=row.get("title_key") or "",
        description=row.get("description_key") or "",
        emoji=row.get("emoji") or "",
        progress=progress,
        rewards=rewards,
        expires_at=expires_at_ms,
        friend_id=row.get("friend_id"),
        friend_display_name=row.get("friend_display_name"),
        status=status_str,  # type: ignore[arg-type]
    )


def _default_catalog(user_id: str) -> list[dict[str, Any]]:
    """Default daily/weekly seed-on-refresh catalogue.

    Mirrors ``buildMockQuestCatalog`` on the frontend but anchored server-side.
    """
    now = datetime.now(UTC)
    iso_in = lambda hours: (now + timedelta(hours=hours)).isoformat()  # noqa: E731
    base_id = lambda slug: f"{user_id}:{slug}"  # noqa: E731
    return [
        {
            "id": base_id("daily-fifty-xp"),
            "user_id": user_id,
            "type": "daily",
            "title_key": "quests.daily.fiftyXp.title",
            "description_key": "quests.daily.fiftyXp.desc",
            "emoji": "⚡",
            "progress_target": 50,
            "progress_unit": "XP",
            "reward_lingots": 5,
            "reward_xp": 10,
            "status": "active",
            "expires_at": iso_in(24),
        },
        {
            "id": base_id("daily-flashcards"),
            "user_id": user_id,
            "type": "daily",
            "title_key": "quests.daily.flashcards.title",
            "description_key": "quests.daily.flashcards.desc",
            "emoji": "🃏",
            "progress_target": 15,
            "progress_unit": "cards",
            "reward_lingots": 3,
            "reward_xp": 5,
            "status": "active",
            "expires_at": iso_in(24),
        },
        {
            "id": base_id("weekly-three-lessons"),
            "user_id": user_id,
            "type": "weekly",
            "title_key": "quests.weekly.threeLessons.title",
            "description_key": "quests.weekly.threeLessons.desc",
            "emoji": "📚",
            "progress_target": 5,
            "progress_unit": "lessons",
            "reward_lingots": 25,
            "reward_xp": 50,
            "reward_streak_shield": True,
            "status": "active",
            "expires_at": iso_in(24 * 7),
        },
    ]


# ─── Routes ──────────────────────────────────────────────────────────────────


@router.get("", response_model=QuestListResponse)
async def list_quests(
    user: CurrentUser,
    repo: QuestRepo,
) -> Any:
    """List the caller's quests in any non-deleted state."""
    quests_repo = require_repo(repo, "quests")
    with api_error("listing quests"):
        rows = await quests_repo.list_quests(user.id or "")  # type: ignore[arg-type]
        items = [_row_to_quest(r) for r in rows]
        return QuestListResponse(items=items)


@router.post("/{quest_id}/progress", response_model=Quest)
async def bump_progress(
    quest_id: str,
    body: QuestProgressBody,
    user: CurrentUser,
    repo: QuestRepo,
) -> Any:
    """Bump quest progress by ``delta``. Flips to claimable when target hit."""
    quests_repo = require_repo(repo, "quests")
    with api_error("updating quest progress"):
        updated = await quests_repo.update_progress(user.id or "", quest_id, int(body.delta))
        if updated is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Quest not found")
        return _row_to_quest(updated)


@router.post("/{quest_id}/claim", response_model=QuestClaimResponse)
async def claim_quest(
    quest_id: str,
    user: CurrentUser,
    repo: QuestRepo,
    users: UserRepo,
) -> Any:
    """Mark a claimable quest completed and grant its rewards.

    Lingots + XP land on the user row immediately. Ad-free minutes + streak
    shields are recorded as ``reward_granted=true`` on the quest row — those
    subsystems need to read from there until dedicated tables exist.
    """
    quests_repo = require_repo(repo, "quests")
    user_id = user.id or ""
    with api_error("claiming quest"):
        current = await quests_repo.get_quest(user_id, quest_id)
        if current is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Quest not found")
        if current["status"] == "completed":
            return QuestClaimResponse(
                quest=_row_to_quest(current),
                lingots_granted=0,
                xp_granted=0,
                reward_granted=True,
            )
        if current["status"] != "claimable":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Quest is not yet claimable",
            )

        claimed = await quests_repo.claim(user_id, quest_id)
        if claimed is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Quest could not be claimed")

        lingots_inc = int(current.get("reward_lingots") or 0)
        xp_inc = int(current.get("reward_xp") or 0)
        if lingots_inc or xp_inc:
            user_row = await users.get_user_by_id(user_id)
            if user_row is not None:
                patch = {
                    "lingots": int(user_row.get("lingots") or 0) + lingots_inc,
                    "xp": int(user_row.get("xp") or 0) + xp_inc,
                }
                await users.update_user(user_id, patch)

        return QuestClaimResponse(
            quest=_row_to_quest(claimed),
            lingots_granted=lingots_inc,
            xp_granted=xp_inc,
            reward_granted=True,
        )


@router.post("/refresh", response_model=QuestRefreshResponse)
async def refresh_quests(
    user: CurrentUser,
    repo: QuestRepo,
) -> Any:
    """Dev convenience: wipe + re-seed the default daily/weekly catalog."""
    quests_repo = require_repo(repo, "quests")
    user_id = user.id or ""
    with api_error("refreshing quests"):
        removed = await quests_repo.delete_user_quests(user_id)
        rows = _default_catalog(user_id)
        for row in rows:
            row.setdefault("created_at", _now_iso())
            await quests_repo.put_quest(row)
        return QuestRefreshResponse(removed=removed, seeded=len(rows))


# ─── Internal service-to-service routes (lingo-async callbacks) ──────────────


@router.get(
    "/_internal/list",
    response_model=QuestListResponse,
    dependencies=[Depends(require_internal_service)],
)
async def internal_list_quests(user_id: str, repo: QuestRepo) -> Any:
    """List quests for any user_id (service-to-service, no Auth0 JWT required)."""
    quests_repo = require_repo(repo, "quests")
    with api_error("listing quests (internal)"):
        rows = await quests_repo.list_quests(user_id)
        items = [_row_to_quest(r) for r in rows]
        return QuestListResponse(items=items)


@router.post(
    "/_internal/{quest_id}/progress",
    response_model=Quest,
    dependencies=[Depends(require_internal_service)],
)
async def internal_bump_progress(
    quest_id: str, body: InternalProgressBody, repo: QuestRepo
) -> Any:
    """Bump quest progress on behalf of a user (service-to-service)."""
    quests_repo = require_repo(repo, "quests")
    with api_error("updating quest progress (internal)"):
        updated = await quests_repo.update_progress(body.user_id, quest_id, int(body.delta))
        if updated is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Quest not found")
        return _row_to_quest(updated)


__all__ = ["router"]
