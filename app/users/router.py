from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.dependencies import get_current_user, get_registered_user
from app.auth.schemas import TokenPayload
from app.db.protocols import ProgressRepository, SocialRepository, SubscriptionRepository, UserRepository
from app.db.provider import (
    get_deck_repo,
    get_progress_repo,
    get_social_repo,
    get_story_repo,
    get_subscription_repo,
    get_user_repo,
)
from app.shared.errors import api_error
from app.shared.repos import require_repo
from app.users.schemas import (
    DiscoverUsersResponse,
    MeUpdate,
    PublicFriendshipStatus,
    PublicUserSummary,
    SubscriptionCreate,
    SubscriptionItem,
    SubscriptionSettingsPatch,
    UserCreate,
    UserResponse,
    UserSettings,
    UserSettingsPatch,
)
from app.users.subscriptions.content_types.registry import get_content_type_handler
from app.users.subscriptions.types import ContentType

router = APIRouter(tags=["users"])

# Registration uses get_current_user — user.id is None before registration
UnregisteredUser = Annotated[TokenPayload, Depends(get_current_user)]
# All other endpoints require a fully registered user (user.id is set)
CurrentUser = Annotated[TokenPayload, Depends(get_registered_user)]
UserRepo = Annotated[UserRepository, Depends(get_user_repo)]


# ── User record ──────────────────────────────────────────────


@router.post("/me", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register_user(body: UserCreate, user: UnregisteredUser, repo: UserRepo) -> Any:
    """First-time registration: creates the user record linked to their Auth0 identity."""
    with api_error("registering user"):
        existing = await repo.get_user_by_auth0_id(user.sub)
        if existing is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "User already registered")

        taken = await repo.get_user_by_username(body.username)
        if taken is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Username already taken")

        record = await repo.create_user(
            {
                "auth0_id": user.sub,
                "username": body.username,
                "display_name": body.display_name,
            }
        )
    return record


@router.get("/me", response_model=UserResponse)
async def get_me(user: CurrentUser, repo: UserRepo) -> Any:
    with api_error("fetching current user"):
        record = await repo.get_user_by_id(user.id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return record


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_me(user: CurrentUser, repo: UserRepo) -> None:
    """Delete the current user's account record and stored settings."""
    from app.auth.dependencies import invalidate_user_id_cache

    with api_error("deleting current user"):
        existing = await repo.get_user_by_id(user.id)
        if existing is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
        await repo.delete_user(user.id)
    invalidate_user_id_cache(user.sub)


@router.patch("/me", response_model=UserResponse)
async def update_me(body: MeUpdate, user: CurrentUser, repo: UserRepo) -> Any:
    patch = body.model_dump(exclude_none=True)
    if not patch:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty patch body")

    with api_error("updating current user"):
        if "username" in patch:
            taken = await repo.get_user_by_username(patch["username"])
            if taken is not None and taken["id"] != user.id:
                raise HTTPException(status.HTTP_409_CONFLICT, "Username already taken")

        try:
            updated = await repo.update_user(user.id, patch)
        except LookupError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found") from exc
    return updated


@router.get("/u/{username}", response_model=UserResponse)
async def get_user_by_username(username: str, repo: UserRepo) -> Any:
    """Public profile lookup by username (no auth required)."""
    with api_error("fetching user by username"):
        record = await repo.get_user_by_username(username)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return record


# ── User settings ────────────────────────────────────────────


@router.get("/me/settings", response_model=UserSettings)
async def get_settings(user: CurrentUser, repo: UserRepo) -> Any:
    with api_error("fetching user settings"):
        data = await repo.get_settings(user.id)
    if data is None:
        return UserSettings()
    return data


@router.patch("/me/settings", response_model=UserSettings)
async def patch_settings(
    body: UserSettingsPatch,
    user: CurrentUser,
    repo: UserRepo,
) -> Any:
    patch = body.model_dump(exclude_none=True)
    if not patch:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty patch body")
    with api_error("updating user settings"):
        updated = await repo.update_settings(user.id, patch)
    return updated


# ── Subscriptions ────────────────────────────────────────────


@router.get("/me/subscriptions", response_model=list[SubscriptionItem])
async def list_subscriptions(
    user: CurrentUser,
    repo: Annotated[SubscriptionRepository | None, Depends(get_subscription_repo)],
    content_type: str | None = Query(None, description="Filter by type: deck, addon, story"),
) -> Any:
    r = require_repo(repo, "subscription")
    # Fix 10 — validate content_type against the ContentType enum so a typo
    # surfaces as 400 rather than silently returning an empty list.
    if content_type is not None and content_type not in [c.value for c in ContentType]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"contentType must be one of: {[c.value for c in ContentType]}",
        )
    with api_error("listing subscriptions"):
        items = await r.list(user.id, content_type=content_type)
    return [SubscriptionItem(**x) for x in items]


@router.post(
    "/me/subscriptions", response_model=SubscriptionItem, status_code=status.HTTP_201_CREATED
)
async def add_subscription(
    body: SubscriptionCreate,
    user: CurrentUser,
    repo: Annotated[SubscriptionRepository | None, Depends(get_subscription_repo)],
    deck_repo: Annotated[Any, Depends(get_deck_repo)],
    story_repo: Annotated[Any, Depends(get_story_repo)],
) -> Any:
    if body.contentType not in [c.value for c in ContentType]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"contentType must be one of: {[c.value for c in ContentType]}",
        )
    r = require_repo(repo, "subscription")
    handler = get_content_type_handler(
        body.contentType,
        context={"deck_repo": deck_repo, "story_repo": story_repo},
    )
    with api_error("adding subscription"):
        if not await handler.validate_subscription(body.contentId):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"{body.contentType} not found: {body.contentId}",
            )
        await r.add(user.id, body.contentType, body.contentId)
        items = await r.list(user.id, content_type=body.contentType)
        added = next((i for i in items if i["contentId"] == body.contentId), None)
        if not added:
            raise HTTPException(status_code=500, detail="Subscription add failed")
    return SubscriptionItem(**added)


@router.patch(
    "/me/subscriptions/{content_type}/{content_id}",
    response_model=SubscriptionItem,
)
async def update_subscription(
    content_type: str,
    content_id: str,
    body: SubscriptionSettingsPatch,
    user: CurrentUser,
    repo: Annotated[SubscriptionRepository | None, Depends(get_subscription_repo)],
) -> Any:
    if content_type not in [c.value for c in ContentType]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"contentType must be one of: {[c.value for c in ContentType]}",
        )
    r = require_repo(repo, "subscription")
    patch = body.model_dump(exclude_none=True)
    if not patch:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty patch body")
    if "newCardOrder" in patch and patch["newCardOrder"] not in ("ordered", "shuffled"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="newCardOrder must be 'ordered' or 'shuffled'",
        )
    db_patch = {}
    if "enabled" in patch:
        db_patch["enabled"] = patch["enabled"]
    if "newCardsPerDay" in patch:
        db_patch["newCardsPerDay"] = patch["newCardsPerDay"]
    if "newCardOrder" in patch:
        db_patch["newCardOrder"] = patch["newCardOrder"]
    with api_error("updating subscription"):
        updated = await r.update_settings(user.id, content_type, content_id, db_patch)
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found"
            )
        items = await r.list(user.id, content_type=content_type)
        item = next((i for i in items if i["contentId"] == content_id), None)
        if not item:
            raise HTTPException(status_code=500, detail="Subscription not found after update")
    return SubscriptionItem(**item)


@router.delete(
    "/me/subscriptions/{content_type}/{content_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_subscription(
    content_type: str,
    content_id: str,
    user: CurrentUser,
    repo: Annotated[SubscriptionRepository | None, Depends(get_subscription_repo)],
) -> None:
    if content_type not in [c.value for c in ContentType]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"contentType must be one of: {[c.value for c in ContentType]}",
        )
    r = require_repo(repo, "subscription")
    with api_error("removing subscription"):
        await r.remove(user.id, content_type, content_id)


# ── Discover (find-friends + contributors browse) ────────────────────────────
#
# This endpoint backs the Community → People surface and the rewritten
# Contributors page. It is intentionally cheap: a single `list_users` scan
# plus per-row weekly-XP and friendship-status lookups. Pagination is
# offset-based to keep the SQL simple and the SQLite path linear; the
# DynamoDB list_users impl already does its own cursor-based paging.
#
# Filtering:
#   - q: substring match (case-insensitive) on username + display_name.
#   - lang: filter to users whose `learning.learningLanguageId` (or legacy
#     `learningLanguage`) matches the supplied language id.
#   - When `q` is empty, the caller is excluded so they don't show up in
#     their own discover feed. When `q` is set, self-match is allowed so
#     "search for myself" still works.
#
# Blocked users (in either direction) are always excluded.


def _learning_language_from_settings(settings_blob: dict[str, Any] | None) -> str | None:
    if not settings_blob:
        return None
    learning = settings_blob.get("learning")
    if isinstance(learning, dict):
        v = learning.get("learningLanguageId")
        if v:
            return str(v)
    legacy = settings_blob.get("learningLanguage")
    return str(legacy) if legacy else None


async def _weekly_xp(progress: ProgressRepository | None, user_id: str) -> int:
    if progress is None:
        return 0
    from datetime import UTC, date, datetime, timedelta

    today = datetime.now(UTC).date()
    since = (today - timedelta(days=6)).isoformat()
    until = today.isoformat()
    try:
        rows = await progress.get_day_rollups(user_id, since, until)
    except Exception:  # noqa: BLE001  — progress impl may not exist locally
        return 0
    return sum(int(r.get("xpEarned") or 0) for r in rows)


async def _discover_friendship_status(
    social: SocialRepository | None, me_id: str, other_id: str
) -> PublicFriendshipStatus:
    if me_id == other_id:
        return "self"
    if social is None:
        return "none"
    if await social.is_blocked(me_id, other_id):
        return "blocked"
    if await social.is_blocked(other_id, me_id):
        return "blocked"
    if await social.is_friend(me_id, other_id):
        return "friend"
    if await social.get_friend_request(me_id, other_id):
        return "request_out"
    if await social.get_friend_request(other_id, me_id):
        return "request_in"
    return "none"


@router.get("/discover", response_model=DiscoverUsersResponse)
async def discover_users(
    user: CurrentUser,
    repo: UserRepo,
    social: Annotated[SocialRepository | None, Depends(get_social_repo)],
    progress: Annotated[ProgressRepository | None, Depends(get_progress_repo)],
    q: str | None = Query(None, description="Substring match on username + display_name"),
    lang: str | None = Query(None, description="Filter by learning language id"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> Any:
    """Public learner directory powering find-friends + contributors browse.

    Excludes blocked users (both directions) and the caller themselves when
    `q` is empty. Returns one ``PublicUserSummary`` per row with friendship
    status pre-computed.
    """
    with api_error("listing users for discover"):
        # Overfetch defensively — list_users is bounded server-side. The
        # SQLite repo caps internally, the DynamoDB repo paginates via cursor.
        # 500 covers the local dev dataset (20 seeded users) and is well
        # under SQLite's hard limit.
        records, _ = await repo.list_users(limit=500)

        normalized_q = (q or "").strip().lower()
        lang_filter = (lang or "").strip().lower() or None
        candidates: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
        for record in records:
            if record.get("status") == "banned":
                continue
            if not normalized_q and record["id"] == user.id:
                continue
            if normalized_q:
                hay = (
                    f"{record.get('username') or ''} "
                    f"{record.get('display_name') or ''}"
                ).lower()
                if normalized_q not in hay:
                    continue
            settings_blob = await repo.get_settings(record["id"])
            if lang_filter:
                user_lang = (_learning_language_from_settings(settings_blob) or "").lower()
                if user_lang != lang_filter:
                    continue
            candidates.append((record, settings_blob))

        # Pull friendship status + weekly XP for the full filtered set so we
        # can sort by XP server-side (contributors page expects this).
        enriched: list[tuple[dict[str, Any], dict[str, Any] | None, int, PublicFriendshipStatus]] = []
        for record, settings_blob in candidates:
            fs = await _discover_friendship_status(social, user.id, record["id"])
            if fs == "blocked":
                continue
            weekly = await _weekly_xp(progress, record["id"])
            enriched.append((record, settings_blob, weekly, fs))

        # Sort: weekly_xp DESC, then streak DESC, then username for stability.
        enriched.sort(
            key=lambda p: (-p[2], -int(p[0].get("streak") or 0), p[0].get("username") or "")
        )

        total = len(enriched)
        sliced = enriched[offset : offset + limit]
        users_out = [
            PublicUserSummary(
                auth0_id=record.get("auth0_id") or "",
                user_id=record["id"],
                username=record["username"],
                display_name=record["display_name"],
                profile_picture_key=record.get("profile_picture_key"),
                learning_language=_learning_language_from_settings(settings_blob),
                weekly_xp=weekly,
                streak_days=int(record.get("streak") or 0),
                friendship_status=fs,
            )
            for record, settings_blob, weekly, fs in sliced
        ]
        has_more = (offset + len(sliced)) < total
    return DiscoverUsersResponse(users=users_out, total=total, has_more=has_more)
