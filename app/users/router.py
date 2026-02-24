from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.dependencies import get_current_user, get_registered_user
from app.auth.schemas import TokenPayload
from app.db.provider import get_deck_repo, get_story_repo, get_subscription_repo, get_user_repo
from app.db.protocols import SubscriptionRepository, UserRepository
from app.users.schemas import (
    MeUpdate,
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
    record = await repo.get_user_by_id(user.id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return record


@router.patch("/me", response_model=UserResponse)
async def update_me(body: MeUpdate, user: CurrentUser, repo: UserRepo) -> Any:
    patch = body.model_dump(exclude_none=True)
    if not patch:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty patch body")

    if "username" in patch:
        taken = await repo.get_user_by_username(patch["username"])
        if taken is not None and taken["id"] != user.id:
            raise HTTPException(status.HTTP_409_CONFLICT, "Username already taken")

    try:
        updated = await repo.update_user(user.id, patch)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return updated


@router.get("/u/{username}", response_model=UserResponse)
async def get_user_by_username(username: str, repo: UserRepo) -> Any:
    """Public profile lookup by username (no auth required)."""
    record = await repo.get_user_by_username(username)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return record


# ── User settings ────────────────────────────────────────────


@router.get("/me/settings", response_model=UserSettings)
async def get_settings(user: CurrentUser, repo: UserRepo) -> Any:
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
    updated = await repo.update_settings(user.id, patch)
    return updated


# ── Subscriptions ────────────────────────────────────────────


def _require_subscription_repo(
    repo: SubscriptionRepository | None,
) -> SubscriptionRepository:
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Subscription storage not configured",
        )
    return repo


@router.get("/me/subscriptions", response_model=list[SubscriptionItem])
async def list_subscriptions(
    user: CurrentUser,
    repo: Annotated[SubscriptionRepository | None, Depends(get_subscription_repo)],
    content_type: str | None = Query(None, description="Filter by type: deck, addon, story"),
) -> Any:
    r = _require_subscription_repo(repo)
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
    r = _require_subscription_repo(repo)
    handler = get_content_type_handler(
        body.contentType,
        context={"deck_repo": deck_repo, "story_repo": story_repo},
    )
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
    r = _require_subscription_repo(repo)
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
    updated = await r.update_settings(user.id, content_type, content_id, db_patch)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found")
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
    r = _require_subscription_repo(repo)
    await r.remove(user.id, content_type, content_id)
