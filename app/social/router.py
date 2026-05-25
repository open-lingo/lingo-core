"""Social API — friends, blocks, leaderboards, public profiles.

All write endpoints are gated by ``get_community_user`` (which excludes
community-banned users) per the maintainer's design.

SQLite-first implementation; the production backend lives in
``app/db/dynamo/social.py`` (currently a stub).
"""

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.dependencies import get_community_user, get_current_user_optional
from app.auth.schemas import TokenPayload
from app.db.protocols import SocialRepository, UserRepository
from app.db.provider import get_social_repo, get_user_repo
from app.social.schemas import (
    ActivityFeedResponse,
    BlockedUserItem,
    FriendItem,
    FriendRequestCreate,
    FriendRequestItem,
    FriendRequestsResponse,
    FriendRequestStatus,
    FriendshipStatus,
    LeaderboardEntry,
    LeaderboardResponse,
    MyLeaderboardSlot,
    MyLeaderboardSummary,
    PublicProfileResponse,
)

router = APIRouter(tags=["social"])

CommunityUser = Annotated[TokenPayload, Depends(get_community_user)]
OptionalUser = Annotated[TokenPayload | None, Depends(get_current_user_optional)]
SocialRepo = Annotated[SocialRepository | None, Depends(get_social_repo)]
UserRepo = Annotated[UserRepository, Depends(get_user_repo)]


# ── Helpers ────────────────────────────────────────────────────────────────


def _require_social_repo(repo: SocialRepository | None) -> SocialRepository:
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Social storage not configured",
        )
    return repo


def _week_bucket(lang: str, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    iso = now.isocalendar()
    return f"{lang}#{iso.year:04d}-W{iso.week:02d}"


def _month_bucket(lang: str, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    return f"{lang}#{now.year:04d}-{now.month:02d}"


async def _learning_lang_for(
    user_id: str, users: UserRepository
) -> str | None:
    """Pull the user's current learning language from settings, if any."""
    settings_blob = await users.get_settings(user_id)
    if not settings_blob:
        return None
    learning = settings_blob.get("learning") or {}
    lang = learning.get("learningLanguageId") or settings_blob.get("learningLanguage")
    return str(lang) if lang else None


def _social_settings(settings_blob: dict[str, Any] | None) -> dict[str, Any]:
    """Pull the social subtree from settings with the documented defaults."""
    base = {
        "visibility": "friends",
        "allow_friend_requests": True,
        "show_on_leaderboard": False,
        "show_activity_feed": True,
    }
    if not settings_blob:
        return base
    raw = settings_blob.get("social")
    if isinstance(raw, dict):
        base.update({k: raw[k] for k in raw if k in base})
    return base


# ── Friends ────────────────────────────────────────────────────────────────


@router.get("/friends", response_model=list[FriendItem])
async def list_friends(
    user: CommunityUser,
    repo: SocialRepo,
    users: UserRepo,
) -> Any:
    """List the current user's friends with display metadata."""
    r = _require_social_repo(repo)
    rows = await r.list_friends(user.id)
    out: list[FriendItem] = []
    for row in rows:
        other = await users.get_user_by_id(row["other_id"])
        if not other:
            continue
        out.append(
            FriendItem(
                user_id=other["id"],
                username=other["username"],
                display_name=other.get("display_name") or other["username"],
                profile_picture_key=other.get("profile_picture_key"),
                xp=int(other.get("xp") or 0),
                streak=int(other.get("streak") or 0),
                lastActiveAt=other.get("last_active_date")
                or other.get("lastActiveDate"),
                friendedAt=row["created_at"],
            )
        )
    return out


@router.get("/friends/requests", response_model=FriendRequestsResponse)
async def list_friend_requests(
    user: CommunityUser,
    repo: SocialRepo,
    users: UserRepo,
) -> Any:
    r = _require_social_repo(repo)
    bundle = await r.list_friend_requests(user.id)

    async def _to_items(rows: list[dict[str, Any]]) -> list[FriendRequestItem]:
        items: list[FriendRequestItem] = []
        for row in rows:
            other = await users.get_user_by_id(row["other_id"])
            if not other:
                continue
            items.append(
                FriendRequestItem(
                    user_id=other["id"],
                    username=other["username"],
                    display_name=other.get("display_name") or other["username"],
                    requestedAt=row["created_at"],
                )
            )
        return items

    return FriendRequestsResponse(
        incoming=await _to_items(bundle["incoming"]),
        outgoing=await _to_items(bundle["outgoing"]),
    )


@router.post("/friends/requests", response_model=FriendRequestStatus)
async def send_friend_request(
    body: FriendRequestCreate,
    user: CommunityUser,
    repo: SocialRepo,
    users: UserRepo,
) -> Any:
    """Send a friend request, resolving by username or user UUID."""
    r = _require_social_repo(repo)
    if not body.toUsername and not body.toUserId:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "toUsername or toUserId is required",
        )

    target: dict[str, Any] | None = None
    if body.toUsername:
        target = await users.get_user_by_username(body.toUsername)
    elif body.toUserId:
        target = await users.get_user_by_id(body.toUserId)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    target_id = target["id"]
    if target_id == user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot friend yourself")

    # Either party blocking blocks the request silently.
    if await r.is_blocked(user.id, target_id):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "You have blocked this user; unblock to send a request",
        )
    if await r.is_blocked(target_id, user.id):
        # Match a typical "soft fail" UX so blockers stay invisible.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    # Honor target's allow_friend_requests setting.
    target_settings = await users.get_settings(target_id)
    target_social = _social_settings(target_settings)
    if not target_social["allow_friend_requests"]:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "User is not accepting friend requests",
        )

    # Dedup: if we're already friends or have a pending request either way,
    # surface the current state rather than creating a duplicate.
    existing_out = await r.get_relationship(user.id, target_id)
    if existing_out:
        kind = existing_out["kind"]
        if kind == "FRIEND":
            return FriendRequestStatus(status="accepted")
        if kind in ("REQUEST_OUT", "REQUEST_IN"):
            return FriendRequestStatus(status="exists")

    await r.send_friend_request(user.id, target_id)
    return FriendRequestStatus(status="pending")


@router.post(
    "/friends/requests/{requester_id}/accept",
    response_model=FriendRequestStatus,
)
async def accept_friend_request(
    requester_id: str,
    user: CommunityUser,
    repo: SocialRepo,
) -> Any:
    r = _require_social_repo(repo)
    ok = await r.accept_friend_request(user.id, requester_id)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No pending friend request")
    return FriendRequestStatus(status="accepted")


@router.delete(
    "/friends/requests/{other_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_friend_request(
    other_id: str,
    user: CommunityUser,
    repo: SocialRepo,
) -> None:
    """Decline an incoming request OR cancel an outgoing one."""
    r = _require_social_repo(repo)
    deleted = await r.delete_friend_request(user.id, other_id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No pending request to remove")


@router.delete(
    "/friends/{friend_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unfriend(
    friend_id: str,
    user: CommunityUser,
    repo: SocialRepo,
) -> None:
    r = _require_social_repo(repo)
    deleted = await r.unfriend(user.id, friend_id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not friends")


# ── Blocks ─────────────────────────────────────────────────────────────────


@router.post("/blocks/{user_id}", status_code=status.HTTP_200_OK)
async def block_user(
    user_id: str,
    user: CommunityUser,
    repo: SocialRepo,
    users: UserRepo,
) -> dict[str, str]:
    if user_id == user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot block yourself")
    target = await users.get_user_by_id(user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    r = _require_social_repo(repo)
    await r.block_user(user.id, user_id)
    return {"status": "blocked"}


@router.delete("/blocks/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unblock_user(
    user_id: str,
    user: CommunityUser,
    repo: SocialRepo,
) -> None:
    r = _require_social_repo(repo)
    deleted = await r.unblock_user(user.id, user_id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not blocked")


@router.get("/blocks", response_model=list[BlockedUserItem])
async def list_blocks(
    user: CommunityUser,
    repo: SocialRepo,
    users: UserRepo,
) -> Any:
    r = _require_social_repo(repo)
    rows = await r.list_blocks(user.id)
    out: list[BlockedUserItem] = []
    for row in rows:
        other = await users.get_user_by_id(row["other_id"])
        if not other:
            continue
        out.append(
            BlockedUserItem(
                user_id=other["id"],
                username=other["username"],
                display_name=other.get("display_name") or other["username"],
                blockedAt=row["created_at"],
            )
        )
    return out


# ── Leaderboards ───────────────────────────────────────────────────────────


async def _leaderboard_entries(
    repo: SocialRepository,
    users: UserRepository,
    bucket: str,
    limit: int,
    offset: int,
) -> list[LeaderboardEntry]:
    rows = await repo.get_leaderboard(bucket, limit=limit, offset=offset)
    out: list[LeaderboardEntry] = []
    rank = offset + 1
    for row in rows:
        u = await users.get_user_by_id(row["user_id"])
        if not u:
            rank += 1
            continue
        out.append(
            LeaderboardEntry(
                user_id=u["id"],
                username=u["username"],
                display_name=u.get("display_name") or u["username"],
                profile_picture_key=u.get("profile_picture_key"),
                xp_this_period=int(row["xp"]),
                rank=rank,
            )
        )
        rank += 1
    return out


@router.get(
    "/leaderboards/{lang}/weekly",
    response_model=LeaderboardResponse,
)
async def weekly_leaderboard(
    lang: str,
    user: CommunityUser,
    repo: SocialRepo,
    users: UserRepo,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> Any:
    r = _require_social_repo(repo)
    bucket = _week_bucket(lang)
    entries = await _leaderboard_entries(r, users, bucket, limit, offset)
    me = await r.get_user_leaderboard_entry(bucket, user.id)
    return LeaderboardResponse(
        bucket=bucket,
        entries=entries,
        total=me["total"] if me else len(entries),
        my_rank=me["rank"] if me else None,
    )


@router.get(
    "/leaderboards/{lang}/monthly",
    response_model=LeaderboardResponse,
)
async def monthly_leaderboard(
    lang: str,
    user: CommunityUser,
    repo: SocialRepo,
    users: UserRepo,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> Any:
    r = _require_social_repo(repo)
    bucket = _month_bucket(lang)
    entries = await _leaderboard_entries(r, users, bucket, limit, offset)
    me = await r.get_user_leaderboard_entry(bucket, user.id)
    return LeaderboardResponse(
        bucket=bucket,
        entries=entries,
        total=me["total"] if me else len(entries),
        my_rank=me["rank"] if me else None,
    )


@router.get("/leaderboards/friends", response_model=LeaderboardResponse)
async def friends_leaderboard(
    user: CommunityUser,
    repo: SocialRepo,
    users: UserRepo,
    lang: str | None = Query(
        None, description="Override language; defaults to user's learning language"
    ),
) -> Any:
    r = _require_social_repo(repo)
    chosen_lang = lang or await _learning_lang_for(user.id, users)
    if not chosen_lang:
        return LeaderboardResponse(
            bucket="",
            entries=[],
            total=0,
            my_rank=None,
        )
    bucket = _week_bucket(chosen_lang)
    rows = await r.get_friends_leaderboard(user.id, bucket)
    entries: list[LeaderboardEntry] = []
    rank = 1
    my_rank: int | None = None
    for row in rows:
        u = await users.get_user_by_id(row["user_id"])
        if not u:
            rank += 1
            continue
        entries.append(
            LeaderboardEntry(
                user_id=u["id"],
                username=u["username"],
                display_name=u.get("display_name") or u["username"],
                profile_picture_key=u.get("profile_picture_key"),
                xp_this_period=int(row["xp"]),
                rank=rank,
            )
        )
        if u["id"] == user.id:
            my_rank = rank
        rank += 1
    return LeaderboardResponse(
        bucket=bucket,
        entries=entries,
        total=len(entries),
        my_rank=my_rank,
    )


@router.get("/leaderboards/me", response_model=MyLeaderboardSummary)
async def my_leaderboard(
    user: CommunityUser,
    repo: SocialRepo,
    users: UserRepo,
    lang: str | None = Query(
        None, description="Override language; defaults to user's learning language"
    ),
) -> Any:
    r = _require_social_repo(repo)
    chosen_lang = lang or await _learning_lang_for(user.id, users)
    if not chosen_lang:
        return MyLeaderboardSummary(weekly=None, monthly=None, lang=None)

    week_bucket = _week_bucket(chosen_lang)
    month_bucket = _month_bucket(chosen_lang)
    week_entry = await r.get_user_leaderboard_entry(week_bucket, user.id)
    month_entry = await r.get_user_leaderboard_entry(month_bucket, user.id)

    return MyLeaderboardSummary(
        weekly=MyLeaderboardSlot(
            bucket=week_bucket,
            xp=week_entry["xp"] if week_entry else 0,
            rank=week_entry["rank"] if week_entry else None,
            total=week_entry["total"] if week_entry else 0,
        ),
        monthly=MyLeaderboardSlot(
            bucket=month_bucket,
            xp=month_entry["xp"] if month_entry else 0,
            rank=month_entry["rank"] if month_entry else None,
            total=month_entry["total"] if month_entry else 0,
        ),
        lang=chosen_lang,
    )


# ── Public profile ─────────────────────────────────────────────────────────


async def _friendship_status(
    repo: SocialRepository,
    viewer_id: str,
    target_id: str,
) -> FriendshipStatus:
    if viewer_id == target_id:
        return "self"
    if await repo.is_blocked(viewer_id, target_id):
        return "blocked"
    rel = await repo.get_relationship(viewer_id, target_id)
    if rel is None:
        return "none"
    if rel["kind"] == "FRIEND":
        return "friend"
    if rel["kind"] == "REQUEST_OUT":
        return "request_out"
    if rel["kind"] == "REQUEST_IN":
        return "request_in"
    return "none"


@router.get("/profiles/{username}", response_model=PublicProfileResponse)
async def get_public_profile(
    username: str,
    viewer: OptionalUser,
    repo: SocialRepo,
    users: UserRepo,
) -> Any:
    record = await users.get_user_by_username(username)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    target_id = record["id"]
    target_settings = await users.get_settings(target_id)
    social_cfg = _social_settings(target_settings)
    learning_lang = (
        (target_settings.get("learning") or {}).get("learningLanguageId")
        if target_settings
        else None
    ) or (target_settings.get("learningLanguage") if target_settings else None)

    friendship: FriendshipStatus | None = None
    viewer_id = viewer.id if viewer else None
    if viewer_id:
        if repo is not None:
            friendship = await _friendship_status(repo, viewer_id, target_id)
        else:
            friendship = "self" if viewer_id == target_id else "none"

    # Visibility enforcement. Self can always see.
    visibility = social_cfg["visibility"]
    if visibility == "private" and friendship != "self":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if visibility == "friends" and friendship not in ("self", "friend"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    return PublicProfileResponse(
        user_id=record["id"],
        username=record["username"],
        display_name=record.get("display_name") or record["username"],
        profile_picture_key=record.get("profile_picture_key"),
        bio=record.get("bio"),
        learning_language=learning_lang,
        joined_at=record.get("created_at") or "",
        streak=int(record.get("streak") or 0),
        xp=int(record.get("xp") or 0),
        friendship_status=friendship,
    )


# ── Activity feed (stub) ───────────────────────────────────────────────────


@router.get("/activity", response_model=ActivityFeedResponse)
async def activity_feed(
    user: CommunityUser,
    cursor: str | None = None,
) -> Any:
    """Activity feed — intentionally a stub for now.

    TODO: implement once the lazy-concept-rollup recompute lands and the
    progress sync hook can emit feed events. The FE wires to this stable
    endpoint so the integration point is fixed.
    """
    _ = (user, cursor)
    return ActivityFeedResponse(items=[], cursor=None)
