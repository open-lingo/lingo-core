"""Social API — friends, blocks, leaderboards, public profiles, activity feed
(with reactions), league spotlight, streak snapshot, invites, threads, and
friend-quest helpers.

Reads route through ``SocialRepository`` + ``UserRepository`` + ``ProgressRepository``.
Mutations go through the social repo only; user-row updates (lingots reward, etc.)
go through the user repo. The router owns policy: friend-request idempotency,
block precedence over friend status, invite caps, etc.
"""

import logging
import secrets
import string
import uuid
from datetime import UTC, date, datetime, timedelta
from statistics import median
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.dependencies import get_acting_user, get_community_user
from app.auth.schemas import TokenPayload
from app.db.protocols import DeckRepository, ProgressRepository, SocialRepository, UserRepository
from app.db.provider import get_deck_repo, get_progress_repo, get_social_repo, get_user_repo
from app.shared.errors import api_error
from app.social.leagues import league_for_xp
from app.social.schemas import (
    DEFAULT_AD_FREE_MINUTES_INVITEE,
    DEFAULT_AD_FREE_MINUTES_INVITER,
    DEFAULT_INVITE_BASE_URL,
    DEFAULT_LINGOT_REWARD_INVITEE,
    DEFAULT_LINGOT_REWARD_INVITER,
    DEFAULT_MONTHLY_CAP,
    REACTION_KINDS,
    ActivityFeedResponse,
    ActivityItem,
    ActivityReaction,
    BlockedUserItem,
    FriendItem,
    FriendRequestItem,
    FriendRequestsResponse,
    FriendRequestStatus,
    FriendshipStatus,
    FriendSuggestionItem,
    FriendSuggestionsResponse,
    InviteOfferResponse,
    InviteRedeemResponse,
    LeaderboardEntry,
    LeaderboardResponse,
    LeagueName,
    LeagueSpotlightResponse,
    Message,
    MyLeaderboardSlot,
    MyLeaderboardSummary,
    PublicProfileResponse,
    QuestTargetItem,
    ReactionKind,
    SendFriendRequestBody,
    StreakSnapshotResponse,
    ThreadDetailResponse,
    ThreadItem,
)

logger = logging.getLogger("lingo.social")

router = APIRouter(tags=["social"])

# Honors admin impersonation for non-community-gated reads/writes
# (friend list, requests, leaderboard view).
CurrentUser = Annotated[TokenPayload, Depends(get_acting_user)]
# Kept on JWT identity — community writes (posts, reactions) should
# resolve to the admin's own community-ban status, not the target's.
CommunityUser = Annotated[TokenPayload, Depends(get_community_user)]
SocialRepo = Annotated[SocialRepository, Depends(get_social_repo)]
UserRepo = Annotated[UserRepository, Depends(get_user_repo)]
ProgressRepo = Annotated[ProgressRepository, Depends(get_progress_repo)]
DeckRepo = Annotated[DeckRepository | None, Depends(get_deck_repo)]


def _learning_language_from_settings(settings_blob: dict[str, Any] | None) -> str | None:
    """Extract the learning language id from a user settings dict.

    Supports the modern nested shape (``learning.learningLanguageId``) and the
    legacy flat ``learningLanguage`` key. Matches the helper in
    ``app/users/router.py`` — duplicated here to avoid a cross-router import.
    """
    if not settings_blob:
        return None
    learning = settings_blob.get("learning")
    if isinstance(learning, dict):
        v = learning.get("learningLanguageId")
        if v:
            return str(v)
    legacy = settings_blob.get("learningLanguage")
    return str(legacy) if legacy else None


# ─── Helpers ─────────────────────────────────────────────────────────────────


_INVITE_CODE_ALPHABET = string.ascii_uppercase + string.digits


def _generate_invite_code() -> str:
    return "".join(secrets.choice(_INVITE_CODE_ALPHABET) for _ in range(8))


def _today() -> date:
    return datetime.now(UTC).date()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _yyyymm(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _user_to_friend_item(user: dict[str, Any], friended_at: str) -> FriendItem:
    return FriendItem(
        user_id=user["id"],
        username=user["username"],
        display_name=user["display_name"],
        profile_picture_key=user.get("profile_picture_key"),
        xp=int(user.get("xp") or 0),
        streak=int(user.get("streak") or 0),
        last_active_at=user.get("last_active_date"),
        friended_at=friended_at,
    )


def _user_to_request_item(user: dict[str, Any], when: str) -> FriendRequestItem:
    return FriendRequestItem(
        user_id=user["id"],
        username=user["username"],
        display_name=user["display_name"],
        requested_at=when,
    )


def _league_for_weekly_xp(weekly_xp: int) -> tuple[LeagueName, int, int | None, int | None]:
    """Return (league, tier, promotion_threshold, demotion_threshold).

    Brackets (chosen to match the spec defaults):
      0-99      bronze   (tier 1)
      100-499   silver   (tier 2)
      500-1499  gold     (tier 3)
      1500-4999 diamond  (tier 4)
      5000+     obsidian (tier 4 — display only)
    """
    if weekly_xp < 100:
        return ("bronze", 1, 100, None)
    if weekly_xp < 500:
        return ("silver", 2, 500, 100)
    if weekly_xp < 1500:
        return ("gold", 3, 1500, 500)
    if weekly_xp < 5000:
        return ("diamond", 4, 5000, 1500)
    return ("obsidian", 4, None, 5000)


async def _xp_in_window(progress: ProgressRepository, user_id: str, days: int) -> int:
    today = _today()
    since = (today - timedelta(days=days - 1)).isoformat()
    until = today.isoformat()
    rows = await progress.get_day_rollups(user_id, since, until)
    return sum(int(r.get("xpEarned") or 0) for r in rows)


async def _xp_for_day(progress: ProgressRepository, user_id: str, day: date) -> int:
    rows = await progress.get_day_rollups(user_id, day.isoformat(), day.isoformat())
    return sum(int(r.get("xpEarned") or 0) for r in rows)


async def _friendship_status(social: SocialRepository, me_id: str, other_id: str) -> FriendshipStatus:
    if me_id == other_id:
        return "self"
    if await social.is_blocked(me_id, other_id):
        return "blocked"
    if await social.is_friend(me_id, other_id):
        return "friend"
    out = await social.get_friend_request(me_id, other_id)
    if out:
        return "request_out"
    inc = await social.get_friend_request(other_id, me_id)
    if inc:
        return "request_in"
    return "none"


async def _users_by_ids(users: UserRepository, ids: list[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for uid in ids:
        rec = await users.get_user_by_id(uid)
        if rec:
            out[uid] = rec
    return out


# ─── Friends ─────────────────────────────────────────────────────────────────


@router.get("/friends", response_model=list[FriendItem])
async def list_friends(
    user: CurrentUser,
    social: SocialRepo,
    users: UserRepo,
) -> Any:
    """List the caller's friends."""
    edges = await social.list_friends(user.id)
    out: list[FriendItem] = []
    for edge in edges:
        friend = await users.get_user_by_id(edge["friend_id"])
        if friend:
            out.append(_user_to_friend_item(friend, edge["friended_at"]))
    return out


@router.get("/friends/requests", response_model=FriendRequestsResponse)
async def list_friend_requests(
    user: CurrentUser,
    social: SocialRepo,
    users: UserRepo,
) -> Any:
    incoming_rows, outgoing_rows = await social.list_friend_requests(user.id)
    incoming: list[FriendRequestItem] = []
    for row in incoming_rows:
        u = await users.get_user_by_id(row["from_id"])
        if u:
            incoming.append(_user_to_request_item(u, row["requested_at"]))
    outgoing: list[FriendRequestItem] = []
    for row in outgoing_rows:
        u = await users.get_user_by_id(row["to_id"])
        if u:
            outgoing.append(_user_to_request_item(u, row["requested_at"]))
    return FriendRequestsResponse(incoming=incoming, outgoing=outgoing)


@router.post("/friends/requests", response_model=FriendRequestStatus)
async def send_friend_request(
    body: SendFriendRequestBody,
    user: CurrentUser,
    social: SocialRepo,
    users: UserRepo,
) -> Any:
    """Send a friend request by username or internal user id."""
    target: dict[str, Any] | None = None
    if body.to_user_id:
        target = await users.get_user_by_id(body.to_user_id)
    elif body.to_username:
        target = await users.get_user_by_username(body.to_username)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Target user not found")
    if target["id"] == user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot friend yourself")
    if await social.is_blocked(user.id, target["id"]) or await social.is_blocked(target["id"], user.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Blocked")
    if await social.is_friend(user.id, target["id"]):
        return FriendRequestStatus(status="exists")
    # If the other side already requested us, auto-accept.
    incoming = await social.get_friend_request(target["id"], user.id)
    if incoming is not None:
        await social.add_friend_edge(user.id, target["id"])
        await social.delete_friend_request(target["id"], user.id)
        await social.delete_friend_request(user.id, target["id"])
        return FriendRequestStatus(status="accepted")
    existing = await social.get_friend_request(user.id, target["id"])
    if existing is not None:
        return FriendRequestStatus(status="exists")
    await social.upsert_friend_request(user.id, target["id"])
    return FriendRequestStatus(status="pending")


@router.post("/friends/requests/{requester_id}/accept", response_model=FriendRequestStatus)
async def accept_friend_request(
    requester_id: str,
    user: CurrentUser,
    social: SocialRepo,
    users: UserRepo,
) -> Any:
    req = await social.get_friend_request(requester_id, user.id)
    if req is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No pending request from that user")
    requester = await users.get_user_by_id(requester_id)
    if requester is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Requester not found")
    await social.add_friend_edge(user.id, requester_id)
    await social.delete_friend_request(requester_id, user.id)
    await social.delete_friend_request(user.id, requester_id)
    return FriendRequestStatus(status="accepted")


@router.delete("/friends/requests/{other_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_friend_request(
    other_id: str,
    user: CurrentUser,
    social: SocialRepo,
) -> None:
    """Cancel an outgoing or reject an incoming request — either direction."""
    await social.delete_friend_request(user.id, other_id)
    await social.delete_friend_request(other_id, user.id)


@router.delete("/friends/{friend_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unfriend(
    friend_id: str,
    user: CurrentUser,
    social: SocialRepo,
) -> None:
    await social.remove_friend_edge(user.id, friend_id)


# ─── Blocks ──────────────────────────────────────────────────────────────────


@router.post("/blocks/{user_id}")
async def block_user(
    user_id: str,
    user: CurrentUser,
    social: SocialRepo,
    users: UserRepo,
) -> dict[str, str]:
    target = await users.get_user_by_id(user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if user_id == user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot block yourself")
    await social.block_user(user.id, user_id)
    # Blocking severs the friendship + any open requests.
    await social.remove_friend_edge(user.id, user_id)
    await social.delete_friend_request(user.id, user_id)
    await social.delete_friend_request(user_id, user.id)
    return {"status": "blocked"}


@router.delete("/blocks/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unblock_user(
    user_id: str,
    user: CurrentUser,
    social: SocialRepo,
) -> None:
    await social.unblock_user(user.id, user_id)


@router.get("/blocks", response_model=list[BlockedUserItem])
async def list_blocks(
    user: CurrentUser,
    social: SocialRepo,
    users: UserRepo,
) -> Any:
    rows = await social.list_blocks(user.id)
    out: list[BlockedUserItem] = []
    for row in rows:
        u = await users.get_user_by_id(row["blocked_id"])
        if u:
            out.append(
                BlockedUserItem(
                    user_id=u["id"],
                    username=u["username"],
                    display_name=u["display_name"],
                    blocked_at=row["blocked_at"],
                )
            )
    return out


# ─── Leaderboards ────────────────────────────────────────────────────────────


async def _build_leaderboard(
    *,
    user: TokenPayload,
    users: UserRepository,
    progress: ProgressRepository,
    bucket: str,
    window_days: int,
    cohort_ids: list[str] | None,
    limit: int,
    offset: int,
) -> LeaderboardResponse:
    """Compute XP totals for the cohort and rank them. ``cohort_ids=None``
    means "everyone we know about" (small dev cohort)."""
    if cohort_ids is None:
        # SQLite list_users overfetches; cap defensively.
        records, _ = await users.list_users(limit=500)
    else:
        records = []
        for uid in cohort_ids:
            r = await users.get_user_by_id(uid)
            if r:
                records.append(r)

    pairs: list[tuple[dict[str, Any], int]] = []
    for record in records:
        xp = await _xp_in_window(progress, record["id"], window_days)
        pairs.append((record, xp))

    pairs.sort(key=lambda p: (-p[1], p[0]["username"]))
    ranked: list[LeaderboardEntry] = []
    my_rank: int | None = None
    for idx, (record, xp) in enumerate(pairs, start=1):
        if record["id"] == user.id:
            my_rank = idx
        ranked.append(
            LeaderboardEntry(
                user_id=record["id"],
                username=record["username"],
                display_name=record["display_name"],
                profile_picture_key=record.get("profile_picture_key"),
                xp_this_period=xp,
                rank=idx,
            )
        )

    sliced = ranked[offset : offset + limit]
    return LeaderboardResponse(
        bucket=bucket,
        entries=sliced,
        total=len(ranked),
        my_rank=my_rank,
    )


@router.get("/leaderboards/weekly", response_model=LeaderboardResponse)
async def leaderboard_weekly(
    user: CurrentUser,
    users: UserRepo,
    progress: ProgressRepo,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    lang: str | None = None,
) -> Any:
    return await _build_leaderboard(
        user=user,
        users=users,
        progress=progress,
        bucket=f"weekly:{lang or 'all'}",
        window_days=7,
        cohort_ids=None,
        limit=limit,
        offset=offset,
    )


@router.get("/leaderboards/monthly", response_model=LeaderboardResponse)
async def leaderboard_monthly(
    user: CurrentUser,
    users: UserRepo,
    progress: ProgressRepo,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    lang: str | None = None,
) -> Any:
    return await _build_leaderboard(
        user=user,
        users=users,
        progress=progress,
        bucket=f"monthly:{lang or 'all'}",
        window_days=30,
        cohort_ids=None,
        limit=limit,
        offset=offset,
    )


@router.get("/leaderboards/friends", response_model=LeaderboardResponse)
async def leaderboard_friends(
    user: CurrentUser,
    social: SocialRepo,
    users: UserRepo,
    progress: ProgressRepo,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    lang: str | None = None,
) -> Any:
    edges = await social.list_friends(user.id)
    cohort = [e["friend_id"] for e in edges] + [user.id]
    return await _build_leaderboard(
        user=user,
        users=users,
        progress=progress,
        bucket=f"friends:{lang or 'all'}",
        window_days=7,
        cohort_ids=cohort,
        limit=limit,
        offset=offset,
    )


@router.get("/leaderboards/me", response_model=MyLeaderboardSummary)
async def leaderboard_me(
    user: CurrentUser,
    users: UserRepo,
    progress: ProgressRepo,
    lang: str | None = None,
) -> Any:
    weekly = await _build_leaderboard(
        user=user,
        users=users,
        progress=progress,
        bucket=f"weekly:{lang or 'all'}",
        window_days=7,
        cohort_ids=None,
        limit=1,
        offset=0,
    )
    monthly = await _build_leaderboard(
        user=user,
        users=users,
        progress=progress,
        bucket=f"monthly:{lang or 'all'}",
        window_days=30,
        cohort_ids=None,
        limit=1,
        offset=0,
    )
    my_weekly_xp = await _xp_in_window(progress, user.id, 7)
    my_monthly_xp = await _xp_in_window(progress, user.id, 30)
    return MyLeaderboardSummary(
        weekly=MyLeaderboardSlot(bucket=weekly.bucket, xp=my_weekly_xp, rank=weekly.my_rank, total=weekly.total),
        monthly=MyLeaderboardSlot(bucket=monthly.bucket, xp=my_monthly_xp, rank=monthly.my_rank, total=monthly.total),
        lang=lang,
    )


@router.get("/leaderboards/spotlight", response_model=LeagueSpotlightResponse)
async def leaderboard_spotlight(
    user: CurrentUser,
    social: SocialRepo,
    users: UserRepo,
    progress: ProgressRepo,
    lang: str | None = None,
) -> Any:
    """League view: my league band, today vs yesterday rank, podium, friend median XP."""
    weekly_xp = await _xp_in_window(progress, user.id, 7)
    league, tier, promo, demo = _league_for_weekly_xp(weekly_xp)

    # Build the all-users weekly leaderboard once so we can pull the podium +
    # find me, and re-rank as of yesterday for the rank delta.
    board = await _build_leaderboard(
        user=user,
        users=users,
        progress=progress,
        bucket=f"weekly:{lang or 'all'}",
        window_days=7,
        cohort_ids=None,
        limit=500,
        offset=0,
    )

    me_row: LeaderboardEntry | None = None
    for entry in board.entries:
        if entry.user_id == user.id:
            me_row = entry
            break

    # Compute yesterday's rank — same cohort, but using the 7-day window ending
    # yesterday.
    yesterday_records, _ = await users.list_users(limit=500)
    yest_today = _today() - timedelta(days=1)
    yest_since = (yest_today - timedelta(days=6)).isoformat()
    yest_until = yest_today.isoformat()
    yest_pairs: list[tuple[str, int]] = []
    for record in yesterday_records:
        rows = await progress.get_day_rollups(record["id"], yest_since, yest_until)
        xp = sum(int(r.get("xpEarned") or 0) for r in rows)
        yest_pairs.append((record["id"], xp))
    yest_pairs.sort(key=lambda p: (-p[1], p[0]))
    rank_yesterday: int | None = None
    for idx, (uid, _xp) in enumerate(yest_pairs, start=1):
        if uid == user.id:
            rank_yesterday = idx
            break

    rank_delta = 0
    if rank_yesterday is not None and board.my_rank is not None:
        rank_delta = rank_yesterday - board.my_rank

    # Build 7-day arrays for the caller and the friend-median chart.
    # Index 0 = 6 days ago, index 6 = today.
    today = _today()
    my_daily_xp: list[int] = []
    for days_back in range(6, -1, -1):
        day = today - timedelta(days=days_back)
        my_daily_xp.append(await _xp_for_day(progress, user.id, day))

    edges = await social.list_friends(user.id)
    friend_daily_xp: list[int] = []
    for days_back in range(6, -1, -1):
        day = today - timedelta(days=days_back)
        day_xps: list[int] = []
        for edge in edges:
            day_xps.append(await _xp_for_day(progress, edge["friend_id"], day))
        friend_daily_xp.append(int(median(day_xps)) if day_xps else 0)

    return LeagueSpotlightResponse(
        league=league,
        league_tier=tier,
        my_row=me_row,
        rank=board.my_rank,
        rank_yesterday=rank_yesterday,
        rank_delta_today=rank_delta,
        daily_xp=my_daily_xp,
        friend_median_daily_xp=friend_daily_xp,
        top_three=board.entries[:3],
        promotion_threshold=promo,
        demotion_threshold=demo,
    )


# ─── Streak snapshot ─────────────────────────────────────────────────────────


@router.get("/streak-snapshot", response_model=StreakSnapshotResponse)
async def streak_snapshot(
    user: CurrentUser,
    social: SocialRepo,
    users: UserRepo,
) -> Any:
    me = await users.get_user_by_id(user.id) or {}
    my_streak = int(me.get("streak") or 0)
    edges = await social.list_friends(user.id)
    friend_streaks: list[tuple[dict[str, Any], int]] = []
    for edge in edges:
        u = await users.get_user_by_id(edge["friend_id"])
        if u:
            friend_streaks.append((u, int(u.get("streak") or 0)))
    if friend_streaks:
        fmed = int(median(s for _, s in friend_streaks))
        best_user, best_streak = max(friend_streaks, key=lambda p: p[1])
        return StreakSnapshotResponse(
            my_streak_days=my_streak,
            friend_median_streak_days=fmed,
            best_friend_streak_days=best_streak,
            best_friend_username=best_user["username"],
        )
    return StreakSnapshotResponse(
        my_streak_days=my_streak,
        friend_median_streak_days=0,
        best_friend_streak_days=None,
        best_friend_username=None,
    )


# ─── Public profile ──────────────────────────────────────────────────────────


@router.get("/profiles/{username}", response_model=PublicProfileResponse)
async def get_public_profile(
    username: str,
    user: CurrentUser,
    social: SocialRepo,
    users: UserRepo,
    decks: DeckRepo,
) -> Any:
    target = await users.get_user_by_username(username)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    settings_blob = await users.get_settings(target["id"]) or {}
    learning_language = settings_blob.get("learning_language") or settings_blob.get("learningLanguageId")
    fs = await _friendship_status(social, user.id, target["id"])
    # Authored-decks enrichment — best-effort: fall back to an empty list if
    # the deck repo isn't wired (Dynamo backend in degraded mode, etc.).
    authored_count = 0
    authored_sample: list[dict[str, Any]] = []
    if decks is not None:
        try:
            owned = await decks.list_owned_manifests(target["id"], status="published", exclude_companion=True)
            authored_count = len(owned)
            authored_sample = [
                {
                    "id": m["id"],
                    "name": m.get("name", ""),
                    "language": m.get("languageId"),
                }
                for m in owned[:5]
            ]
        except Exception:  # pragma: no cover - defensive; don't fail the profile
            authored_count = 0
            authored_sample = []
    target_xp = int(target.get("xp") or 0)
    shop_blob = settings_blob.get("shop") if isinstance(settings_blob, dict) else None
    shop_blob = shop_blob if isinstance(shop_blob, dict) else {}
    def _eq(key: str) -> str | None:
        val = shop_blob.get(key)
        return val if isinstance(val, str) and val else None
    return PublicProfileResponse(
        user_id=target["id"],
        username=target["username"],
        display_name=target["display_name"],
        profile_picture_key=target.get("profile_picture_key"),
        bio=target.get("bio"),
        learning_language=learning_language,
        joined_at=target.get("created_at") or _now_iso(),
        streak=int(target.get("streak") or 0),
        xp=target_xp,
        friendship_status=fs,
        lingots=int(target.get("lingots") or 0),
        level=int(target.get("level") or 1),
        last_active_date=target.get("last_active_date"),
        authored_deck_count=authored_count,
        authored_decks_sample=authored_sample,
        league=league_for_xp(target_xp),
        equipped_decorator_id=_eq("equippedDecorator"),
        equipped_title_id=_eq("equippedTitle"),
        equipped_banner_id=_eq("equippedBanner"),
    )


# ─── Activity feed + reactions ───────────────────────────────────────────────


def _summarize_reactions(rows: list[dict[str, Any]], me_id: str) -> list[ActivityReaction]:
    by_kind: dict[str, dict[str, Any]] = {k: {"kind": k, "count": 0, "mine": False} for k in REACTION_KINDS}
    for row in rows:
        kind = row.get("kind")
        if kind not in by_kind:
            continue
        by_kind[kind]["count"] += 1
        if row.get("user_id") == me_id:
            by_kind[kind]["mine"] = True
    return [ActivityReaction(**by_kind[k]) for k in REACTION_KINDS]


@router.get("/activity", response_model=ActivityFeedResponse)
async def list_activity(
    user: CurrentUser,
    social: SocialRepo,
    users: UserRepo,
    progress: ProgressRepo,
    limit: int = Query(20, ge=1, le=50),
    cursor: str | None = None,  # noqa: ARG001 — accepted for FE forward-compat
) -> Any:
    """Real activity feed sourced from each friend's recent progress attempts.

    Per ADR-0001 § Phase 2 (pull-based): query each friend's recent
    ``progress.list_attempts`` via the ``UserAttempts-Index`` GSI on
    ``lingo_progress``, merge + sort desc by ``attemptedAt``, cap at the
    caller's limit (max 50). The activity ``id`` is the underlying
    ``attemptId`` so the existing reactions table (keyed on
    ``activity_id``) keeps working — a friend's "👏" on an attempt reuses
    the same storage path as before.

    Cursor pagination is not yet implemented for this pull-based flow —
    cap acts as the page boundary. Returning a null cursor matches the
    FE contract documented in the task.
    """
    with api_error("listing activity feed"):
        edges = await social.list_friends(user.id)
        # Include the caller's own attempts so they see what they just did
        # alongside their friends — mirrors the legacy activity feed.
        actor_ids = [e["friend_id"] for e in edges] + [user.id]
        # Per-actor cap matches the response cap so a single very active
        # friend can't crowd everyone out of the feed.
        per_actor_limit = limit

        merged: list[dict[str, Any]] = []
        for actor_id in actor_ids:
            try:
                attempts, _next = await progress.list_attempts(
                    actor_id, lesson_id=None, limit=per_actor_limit
                )
            except Exception as exc:  # noqa: BLE001 — defensive against missing progress backend
                logger.warning("activity: list_attempts failed for %s: %s", actor_id, exc)
                continue
            for a in attempts:
                merged.append({"actor_id": actor_id, "attempt": a})

        if not merged:
            return ActivityFeedResponse(items=[], cursor=None)

        # Sort newest first; cap at limit.
        merged.sort(key=lambda m: m["attempt"]["attemptedAt"], reverse=True)
        merged = merged[:limit]

        # Hydrate actor metadata + reactions in a single bulk pass.
        unique_actor_ids = list({m["actor_id"] for m in merged})
        actor_map = await _users_by_ids(users, unique_actor_ids)
        activity_ids = [m["attempt"]["attemptId"] for m in merged]
        reactions_by_aid = await social.list_reactions_bulk(activity_ids)

        out: list[ActivityItem] = []
        for m in merged:
            actor = actor_map.get(m["actor_id"])
            if not actor:
                continue
            attempt = m["attempt"]
            attempt_id = attempt["attemptId"]
            rxn_rows = reactions_by_aid.get(attempt_id, [])
            payload: dict[str, Any] = {
                "lessonId": attempt.get("lessonId"),
                "score": attempt.get("score"),
                "passed": bool(attempt.get("passed")),
                "durationSec": attempt.get("durationSec"),
            }
            out.append(
                ActivityItem(
                    id=attempt_id,
                    user_id=actor["id"],
                    username=actor["username"],
                    display_name=actor["display_name"],
                    profile_picture_key=actor.get("profile_picture_key"),
                    kind="lesson_completed",
                    payload=payload,
                    created_at=attempt["attemptedAt"],
                    reactions=_summarize_reactions(rxn_rows, user.id),
                )
            )
        return ActivityFeedResponse(items=out, cursor=None)


@router.post(
    "/activity/{activity_id}/reactions/{kind}",
    response_model=ActivityReaction,
)
async def toggle_activity_reaction(
    activity_id: str,
    kind: ReactionKind,
    user: CurrentUser,
    social: SocialRepo,
) -> Any:
    """Toggle a reaction on an activity item.

    With the pull-based ``/activity`` (sourced from progress attempts),
    the ``activity_id`` is the underlying ``attemptId``. We no longer
    require the id to exist in the legacy ``social_activity`` table —
    the reactions table is keyed on ``activity_id`` regardless, and a
    stale reaction simply never surfaces if its parent attempt is
    deleted (e.g. via Learn → Start over). This matches the
    "rollups are derived" stance from ADR-0001.
    """
    if kind not in REACTION_KINDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown reaction kind")
    mine, count = await social.toggle_reaction(activity_id, user.id, kind)
    return ActivityReaction(kind=kind, count=count, mine=mine)


# ─── Invites ─────────────────────────────────────────────────────────────────


async def _resolve_or_create_invite(social: SocialRepository, owner_id: str) -> dict[str, Any]:
    existing = await social.get_invite_code_for_owner(owner_id)
    if existing:
        return existing
    # Avoid collisions on the (admittedly tiny) global code space.
    for _ in range(8):
        candidate = _generate_invite_code()
        clash = await social.get_invite_code(candidate)
        if clash is None:
            return await social.create_invite_code(owner_id, candidate)
    raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Could not allocate an invite code")


@router.get("/invites/offer", response_model=InviteOfferResponse)
async def get_invite_offer(
    user: CurrentUser,
    social: SocialRepo,
) -> Any:
    row = await _resolve_or_create_invite(social, user.id)
    count = await social.count_redemptions_for_owner_in_month(user.id, _yyyymm(_today()))
    return InviteOfferResponse(
        code=row["code"],
        url=f"{DEFAULT_INVITE_BASE_URL}/{row['code']}",
        lingot_reward_inviter=DEFAULT_LINGOT_REWARD_INVITER,
        lingot_reward_invitee=DEFAULT_LINGOT_REWARD_INVITEE,
        ad_free_minutes_inviter=DEFAULT_AD_FREE_MINUTES_INVITER,
        ad_free_minutes_invitee=DEFAULT_AD_FREE_MINUTES_INVITEE,
        monthly_cap=DEFAULT_MONTHLY_CAP,
        redeemed_count_this_month=count,
        first_lesson_required=True,
    )


@router.post("/invites/redeem/{code}", response_model=InviteRedeemResponse)
async def redeem_invite(
    code: str,
    user: CurrentUser,
    social: SocialRepo,
) -> Any:
    invite = await social.get_invite_code(code)
    if invite is None:
        return InviteRedeemResponse(status="invalid")
    inviter_id = invite["owner_id"]
    if inviter_id == user.id:
        return InviteRedeemResponse(status="self")

    existing = await social.get_redemption(code, user.id)
    if existing is not None:
        # Idempotent: surface whatever state we're in.
        return InviteRedeemResponse(
            status=existing["status"],
            lingot_reward=(DEFAULT_LINGOT_REWARD_INVITEE if existing["status"] == "redeemed" else 0),
            ad_free_minutes=(DEFAULT_AD_FREE_MINUTES_INVITEE if existing["status"] == "redeemed" else 0),
        )

    ym = _yyyymm(_today())
    count = await social.count_redemptions_for_owner_in_month(inviter_id, ym)
    if count >= DEFAULT_MONTHLY_CAP:
        return InviteRedeemResponse(status="cap_reached")

    # Spec: invitee must complete first lesson before the reward unlocks.
    # We persist as pending here; a follow-up "first lesson completed" hook
    # would flip it to redeemed. For now redemption stays pending.
    await social.upsert_redemption(
        {
            "code": code,
            "invitee_id": user.id,
            "inviter_id": inviter_id,
            "status": "pending",
            "redeemed_at": _now_iso(),
            "year_month": ym,
        }
    )
    return InviteRedeemResponse(status="pending", lingot_reward=0, ad_free_minutes=0)


# ─── Threads (stub messaging) ────────────────────────────────────────────────


@router.get("/threads", response_model=list[ThreadItem])
async def list_threads(
    user: CurrentUser,
    social: SocialRepo,
    users: UserRepo,
) -> Any:
    rows = await social.list_threads_for_user(user.id)
    out: list[ThreadItem] = []
    for row in rows:
        other_id = row["user_b_id"] if row["user_a_id"] == user.id else row["user_a_id"]
        other = await users.get_user_by_id(other_id)
        if not other:
            continue
        msgs = await social.list_messages(row["id"])
        last = msgs[-1] if msgs else None
        last_at = datetime.fromisoformat(last["sent_at"]) if last else datetime.fromisoformat(row["updated_at"])
        out.append(
            ThreadItem(
                id=row["id"],
                other_user_id=other["id"],
                other_username=other["username"],
                other_display_name=other["display_name"],
                other_avatar_key=other.get("profile_picture_key"),
                last_message_preview=(last["body"] if last else "")[:120],
                last_message_at=last_at,
                unread_count=0,
            )
        )
    return out


@router.get("/threads/{thread_id}", response_model=ThreadDetailResponse)
async def get_thread_detail(
    thread_id: str,
    user: CurrentUser,
    social: SocialRepo,
    users: UserRepo,
) -> Any:
    thread = await social.get_thread(thread_id)
    if thread is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found")
    if user.id not in (thread["user_a_id"], thread["user_b_id"]):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a participant")
    other_id = thread["user_b_id"] if thread["user_a_id"] == user.id else thread["user_a_id"]
    other = await users.get_user_by_id(other_id)
    if other is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Other participant not found")
    msgs = await social.list_messages(thread_id)
    return ThreadDetailResponse(
        id=thread["id"],
        other_user_id=other["id"],
        other_username=other["username"],
        other_display_name=other["display_name"],
        other_avatar_key=other.get("profile_picture_key"),
        messages=[
            Message(
                id=m["id"],
                thread_id=m["thread_id"],
                sender_id=m["sender_id"],
                body=m["body"],
                sent_at=datetime.fromisoformat(m["sent_at"]),
            )
            for m in msgs
        ],
    )


@router.post("/threads/with/{user_id}", response_model=ThreadItem)
async def open_or_create_thread_with(
    user_id: str,
    user: CurrentUser,
    social: SocialRepo,
    users: UserRepo,
) -> Any:
    """Open (or create) the 1:1 thread between the caller and ``user_id``.

    Idempotent: returns the existing thread when one already exists, otherwise
    creates a fresh row and returns it. Used by the messenger "New
    conversation" picker so the FE never has to special-case "is there a
    thread already?".
    """
    if user_id == user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot message yourself")
    other = await users.get_user_by_id(user_id)
    if other is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    existing = await social.list_threads_for_user(user.id)
    found: dict[str, Any] | None = None
    for row in existing:
        partner_id = row["user_b_id"] if row["user_a_id"] == user.id else row["user_a_id"]
        if partner_id == user_id:
            found = row
            break

    if found is None:
        now = datetime.now(UTC).isoformat()
        found = await social.put_thread(
            {
                "id": str(uuid.uuid4()),
                # Canonicalize ordering so duplicates are impossible — caller's
                # id is always user_a when smaller (string sort), partner
                # otherwise. The query above tolerates either ordering.
                "user_a_id": min(user.id, user_id),
                "user_b_id": max(user.id, user_id),
                "created_at": now,
                "updated_at": now,
            }
        )

    msgs = await social.list_messages(found["id"])
    last = msgs[-1] if msgs else None
    last_at = (
        datetime.fromisoformat(last["sent_at"]) if last else datetime.fromisoformat(found["updated_at"])
    )
    return ThreadItem(
        id=found["id"],
        other_user_id=other["id"],
        other_username=other["username"],
        other_display_name=other["display_name"],
        other_avatar_key=other.get("profile_picture_key"),
        last_message_preview=(last["body"] if last else "")[:120],
        last_message_at=last_at,
        unread_count=0,
    )


# ─── Friend quest helpers ────────────────────────────────────────────────────


@router.get("/quest-targets", response_model=list[QuestTargetItem])
async def quest_targets(
    user: CurrentUser,
    social: SocialRepo,
    users: UserRepo,
    progress: ProgressRepo,
) -> Any:
    me = await users.get_user_by_id(user.id) or {}
    my_streak = int(me.get("streak") or 0)
    my_weekly_xp = await _xp_in_window(progress, user.id, 7)

    edges = await social.list_friends(user.id)
    out: list[QuestTargetItem] = []
    for edge in edges:
        friend = await users.get_user_by_id(edge["friend_id"])
        if not friend:
            continue
        f_streak = int(friend.get("streak") or 0)
        f_weekly_xp = await _xp_in_window(progress, friend["id"], 7)

        reachable: list[str] = []
        # Streak: friend's streak is within +1 day of caller's (≤ caller +1).
        if f_streak <= my_streak + 1:
            reachable.append("streak")
        # Weekly XP: within ±20% of caller's weekly XP. When caller is at 0
        # XP, anyone with 0..50 weekly XP is considered reachable so the bucket
        # isn't empty for brand-new users.
        if my_weekly_xp == 0:
            if f_weekly_xp <= 50:
                reachable.append("weekly_xp")
        else:
            lo = my_weekly_xp * 0.8
            hi = my_weekly_xp * 1.2
            if lo <= f_weekly_xp <= hi:
                reachable.append("weekly_xp")

        if not reachable:
            continue
        out.append(
            QuestTargetItem(
                user_id=friend["id"],
                username=friend["username"],
                display_name=friend["display_name"],
                avatar_key=friend.get("profile_picture_key"),
                streak_days=f_streak,
                level=int(friend.get("level") or 1),
                reachable_for=reachable,
            )
        )
    return out


# ─── Friend suggestions ──────────────────────────────────────────────────────


@router.get("/suggestions", response_model=FriendSuggestionsResponse)
async def list_friend_suggestions(
    user: CommunityUser,
    social: SocialRepo,
    users: UserRepo,
    limit: int = Query(10, ge=1, le=50),
) -> Any:
    """Return non-friend, non-blocked users who share the caller's
    learning language. Capped at ``limit``.

    Sourced from the user directory (``list_users``). Cheap O(N) filter on
    the small dev cohort. The DynamoDB path will overfetch the same way the
    leaderboard helpers do until a dedicated index lands; not a problem at
    MVP scale.
    """
    with api_error("listing friend suggestions"):
        me_settings = await users.get_settings(user.id) or {}
        my_lang = (_learning_language_from_settings(me_settings) or "").lower()

        # Overfetch from the directory then filter. The dev cohort is tiny
        # (<100 users), and the leaderboards helpers already use the same cap.
        records, _ = await users.list_users(limit=500)

        out: list[FriendSuggestionItem] = []
        for record in records:
            if record["id"] == user.id:
                continue
            if record.get("status") == "banned":
                continue
            # Exclude existing friends.
            if await social.is_friend(user.id, record["id"]):
                continue
            # Exclude blocked in either direction.
            if await social.is_blocked(user.id, record["id"]):
                continue
            if await social.is_blocked(record["id"], user.id):
                continue
            # Exclude pending requests (both directions) so we don't suggest
            # someone you've already sent a request to.
            if await social.get_friend_request(user.id, record["id"]):
                continue
            if await social.get_friend_request(record["id"], user.id):
                continue

            settings_blob = await users.get_settings(record["id"])
            their_lang = (_learning_language_from_settings(settings_blob) or "").lower()
            # Filter by shared language when the caller has one set. When the
            # caller has no language pref yet, return language-agnostic
            # suggestions so the surface isn't empty.
            if my_lang and their_lang != my_lang:
                continue

            out.append(
                FriendSuggestionItem(
                    user_id=record["id"],
                    username=record["username"],
                    display_name=record["display_name"],
                    profile_picture_key=record.get("profile_picture_key"),
                    learning_language=_learning_language_from_settings(settings_blob),
                    streak=int(record.get("streak") or 0),
                    xp=int(record.get("xp") or 0),
                    reason=("Same language" if my_lang else "On Open Lingo"),
                )
            )
            if len(out) >= limit:
                break

        return FriendSuggestionsResponse(items=out)


# ─── Internal: helper for tests / seed scripts to mint an activity ───────────


def _new_uuid() -> str:
    return str(uuid.uuid4())
