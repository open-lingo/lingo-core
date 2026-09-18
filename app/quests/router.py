"""Quests API — daily/weekly/random/friend goals with progress + rewards.

The frontend has ``src/features/quests/`` rendering against ``buildMockQuestCatalog``;
this router persists the same shape server-side. Rewards (lingots + XP) are
applied to the user row on claim. Ad-free minutes + streak shields are tracked
on the quest row's ``reward_granted`` flag until those subsystems land —
documented gap, see /docs/.

Catalog generation (2026-09-18): daily/weekly quests are drawn from a fixed
pool (``_DAILY_POOL`` / ``_WEEKLY_POOL``) using a seed of ``user_id + the
calendar day (or ISO week)``, so the same user always gets the same picks
for that day/week no matter which device asks or how many times the bucket
gets lazily regenerated — see ``_seeded_pick``.
"""

import hashlib
import logging
import random
from datetime import UTC, datetime, time, timedelta
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


# ─── Recurring catalog: pools + deterministic per-user picks ────────────────
#
# Reward sizing: lesson_pass_xp defaults to 10 (15 perfect), review_xp to 2
# (XpEconomyConfig, app/platform_settings/schemas.py — admin-tunable, these
# are the un-tuned defaults). Quest rewards are a SMALL top-up on top of
# what the activity already pays, not a replacement for it: a daily quest
# nets roughly half again the XP the underlying reps already earned, so
# claiming feels like a bonus, not the point. Lingots mirror the xp/2
# ratio the original fixed catalog used (5 lingots / 10 xp, 3 / 5, 25 / 50)
# — kept exactly for continuity with any already-live rows. Only the
# heaviest weekly quest carries a streak shield (a real, spendable-in-shop
# reward — see shop/), so it stays rare.
_DAILY_POOL: list[dict[str, Any]] = [
    {
        "slug": "daily-xp-30",
        "title_key": "quests.daily.xp30.title",
        "description_key": "quests.daily.xp30.desc",
        "emoji": "⚡",
        "progress_target": 30,
        "progress_unit": "XP",
        "reward_lingots": 3,
        "reward_xp": 5,
    },
    {
        "slug": "daily-xp-50",
        "title_key": "quests.daily.fiftyXp.title",
        "description_key": "quests.daily.fiftyXp.desc",
        "emoji": "⚡",
        "progress_target": 50,
        "progress_unit": "XP",
        "reward_lingots": 5,
        "reward_xp": 10,
    },
    {
        "slug": "daily-xp-80",
        "title_key": "quests.daily.xp80.title",
        "description_key": "quests.daily.xp80.desc",
        "emoji": "🔥",
        "progress_target": 80,
        "progress_unit": "XP",
        "reward_lingots": 8,
        "reward_xp": 15,
    },
    {
        "slug": "daily-lessons-1",
        "title_key": "quests.daily.oneLesson.title",
        "description_key": "quests.daily.oneLesson.desc",
        "emoji": "📖",
        "progress_target": 1,
        "progress_unit": "lessons",
        "reward_lingots": 2,
        "reward_xp": 5,
    },
    {
        "slug": "daily-lessons-2",
        "title_key": "quests.daily.twoLessons.title",
        "description_key": "quests.daily.twoLessons.desc",
        "emoji": "📚",
        "progress_target": 2,
        "progress_unit": "lessons",
        "reward_lingots": 5,
        "reward_xp": 10,
    },
    {
        "slug": "daily-cards-10",
        "title_key": "quests.daily.tenCards.title",
        "description_key": "quests.daily.tenCards.desc",
        "emoji": "🃏",
        "progress_target": 10,
        "progress_unit": "cards",
        "reward_lingots": 3,
        "reward_xp": 5,
    },
    {
        "slug": "daily-cards-15",
        "title_key": "quests.daily.flashcards.title",
        "description_key": "quests.daily.flashcards.desc",
        "emoji": "🃏",
        "progress_target": 15,
        "progress_unit": "cards",
        "reward_lingots": 4,
        "reward_xp": 8,
    },
    {
        "slug": "daily-cards-20",
        "title_key": "quests.daily.twentyCards.title",
        "description_key": "quests.daily.twentyCards.desc",
        "emoji": "🎴",
        "progress_target": 20,
        "progress_unit": "cards",
        "reward_lingots": 6,
        "reward_xp": 10,
    },
]
# How many daily quests a user sees at once — 3 of the 8 above.
_DAILY_PICK_COUNT = 3

_WEEKLY_POOL: list[dict[str, Any]] = [
    {
        "slug": "weekly-lessons-5",
        "title_key": "quests.weekly.fiveLessons.title",
        "description_key": "quests.weekly.fiveLessons.desc",
        "emoji": "📗",
        "progress_target": 5,
        "progress_unit": "lessons",
        "reward_lingots": 15,
        "reward_xp": 30,
    },
    {
        "slug": "weekly-lessons-10",
        "title_key": "quests.weekly.threeLessons.title",
        "description_key": "quests.weekly.threeLessons.desc",
        "emoji": "📚",
        "progress_target": 10,
        "progress_unit": "lessons",
        "reward_lingots": 25,
        "reward_xp": 50,
        "reward_streak_shield": True,
    },
    {
        "slug": "weekly-cards-50",
        "title_key": "quests.weekly.fiftyCards.title",
        "description_key": "quests.weekly.fiftyCards.desc",
        "emoji": "🎴",
        "progress_target": 50,
        "progress_unit": "cards",
        "reward_lingots": 20,
        "reward_xp": 40,
    },
    {
        "slug": "weekly-xp-200",
        "title_key": "quests.weekly.twoHundredXp.title",
        "description_key": "quests.weekly.twoHundredXp.desc",
        "emoji": "⚡",
        "progress_target": 200,
        "progress_unit": "XP",
        "reward_lingots": 20,
        "reward_xp": 40,
    },
]
# How many weekly quests a user sees at once — 2 of the 4 above.
_WEEKLY_PICK_COUNT = 2


def _seeded_pick(pool: list[dict[str, Any]], seed: str, k: int) -> list[dict[str, Any]]:
    """Deterministically choose ``k`` distinct items from ``pool``.

    Seeded by a string (``user_id`` + a period key), not wall-clock —
    calling this twice with the same seed always returns the same items
    in the same order, so two devices (or a regenerate-on-expiry race)
    agree without coordination. ``hashlib`` (not Python's salted
    ``hash()``) so the seed→pick mapping is stable across processes.
    """
    seed_int = int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16)
    rng = random.Random(seed_int)
    return rng.sample(pool, k)


def _daily_catalog(user_id: str, now: datetime) -> list[dict[str, Any]]:
    """Today's 3 daily quests, picked deterministically by user + calendar day.

    Resets at UTC midnight (the pipeline carries no per-user timezone —
    lesson/review/xp events don't include one — so this is a UTC day, not
    the learner's local day; documented gap, not silently assumed away).
    """
    day_key = now.date().isoformat()
    picks = _seeded_pick(_DAILY_POOL, f"{user_id}:daily:{day_key}", _DAILY_PICK_COUNT)
    expires_at = datetime.combine(now.date() + timedelta(days=1), time.min, tzinfo=UTC)
    rows = []
    for p in picks:
        row = {k: v for k, v in p.items() if k != "slug"}
        row["id"] = f"{user_id}:{p['slug']}:{day_key}"
        row["user_id"] = user_id
        row["type"] = "daily"
        row["status"] = "active"
        row["expires_at"] = expires_at.isoformat()
        rows.append(row)
    return rows


def _weekly_catalog(user_id: str, now: datetime) -> list[dict[str, Any]]:
    """This week's 2 weekly quests, picked deterministically by user + ISO week.

    Resets at UTC Monday 00:00 (same UTC-day-boundary caveat as
    ``_daily_catalog`` — no per-user timezone is available here).
    """
    iso_year, iso_week, iso_weekday = now.isocalendar()
    week_key = f"{iso_year}-W{iso_week:02d}"
    picks = _seeded_pick(_WEEKLY_POOL, f"{user_id}:weekly:{week_key}", _WEEKLY_PICK_COUNT)
    week_start = now.date() - timedelta(days=iso_weekday - 1)
    expires_at = datetime.combine(week_start + timedelta(days=7), time.min, tzinfo=UTC)
    rows = []
    for p in picks:
        row = {k: v for k, v in p.items() if k != "slug"}
        row["id"] = f"{user_id}:{p['slug']}:{week_key}"
        row["user_id"] = user_id
        row["type"] = "weekly"
        row["status"] = "active"
        row["expires_at"] = expires_at.isoformat()
        rows.append(row)
    return rows


def _default_catalog(user_id: str, now: datetime | None = None) -> list[dict[str, Any]]:
    """The full recurring catalogue: today's dailies + this week's weeklies."""
    resolved_now = now or datetime.now(UTC)
    return _daily_catalog(user_id, resolved_now) + _weekly_catalog(user_id, resolved_now)


# ─── Routes ──────────────────────────────────────────────────────────────────


async def _ensure_active_catalog(quests_repo: Any, user_id: str) -> list[dict[str, Any]]:
    """Return the user's quest list, seeding the default catalogue on demand.

    Two triggers fire a seed:
      1. The user has never had any quests minted (fresh signup).
      2. Every previously-minted quest of a given type (daily / weekly) has
         expired — we re-seed that bucket so the user always has something
         to do. Completed/claimed quests aren't replaced until they expire.

    Lazy seeding is the cheap path: no scheduled job to maintain, and a
    user who never hits ``/quests`` never costs us a write.
    """
    now_iso = _now_iso()
    rows = await quests_repo.list_quests(user_id)

    # Drop quests whose expires_at is in the past (regardless of status).
    fresh_rows: list[dict[str, Any]] = []
    expired_ids: list[str] = []
    for r in rows:
        exp = r.get("expires_at") or ""
        if exp and exp < now_iso:
            expired_ids.append(r["id"])
        else:
            fresh_rows.append(r)
    if expired_ids:
        # delete_user_quests is the only batch deleter on the protocol; use a
        # type filter so we only nuke the expired buckets, not non-expired
        # quests of the same type. Implementation note: the protocol method
        # filters by type, not by id list — re-implementing per-id delete on
        # every backend is heavier than just re-seeding stale buckets.
        expired_types = {
            r["type"] for r in rows if r["id"] in set(expired_ids) and r.get("type")
        }
        for t in expired_types:
            await quests_repo.delete_user_quests(user_id, [t])
        # And re-fetch since we just mutated.
        fresh_rows = await quests_repo.list_quests(user_id)

    have_types = {r.get("type") for r in fresh_rows}
    catalog = _default_catalog(user_id)
    missing = [q for q in catalog if q.get("type") not in have_types]

    if missing:
        for row in missing:
            row.setdefault("created_at", now_iso)
            await quests_repo.put_quest(row)
        fresh_rows = await quests_repo.list_quests(user_id)

    return fresh_rows


@router.get("", response_model=QuestListResponse)
async def list_quests(
    user: CurrentUser,
    repo: QuestRepo,
) -> Any:
    """List the caller's quests; seed the default catalogue if missing/expired."""
    quests_repo = require_repo(repo, "quests")
    user_id = user.id or ""
    with api_error("listing quests"):
        rows = await _ensure_active_catalog(quests_repo, user_id)
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
            # claim() is transition-only: None means THIS request did not flip
            # claimable -> completed. Either a concurrent claim won the race, or
            # the quest became unclaimable. Re-read to decide: if it's now
            # completed, respond idempotently with zero grants (the winner already
            # credited the user) — never double-award. Anything else is a 409.
            latest = await quests_repo.get_quest(user_id, quest_id)
            if latest is not None and latest["status"] == "completed":
                return QuestClaimResponse(
                    quest=_row_to_quest(latest),
                    lingots_granted=0,
                    xp_granted=0,
                    reward_granted=True,
                )
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
