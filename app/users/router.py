from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.dependencies import get_current_user
from app.auth.schemas import TokenPayload
from app.db.dependencies import get_user_repo
from app.db.protocols import UserRepository
from app.users.schemas import (
    UserCreate,
    UserResponse,
    UserSettings,
    UserSettingsPatch,
    UserUpdate,
)

router = APIRouter(prefix="/api/core/users/v1", tags=["users"])

CurrentUser = Annotated[TokenPayload, Depends(get_current_user)]
UserRepo = Annotated[UserRepository, Depends(get_user_repo)]


# ── User record ──────────────────────────────────────────────


@router.post("/me", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register_user(body: UserCreate, user: CurrentUser, repo: UserRepo) -> Any:
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
    """Return the current user's record. 404 if not yet registered."""
    record = await repo.get_user_by_auth0_id(user.sub)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not registered")
    return record


@router.patch("/me", response_model=UserResponse)
async def update_me(body: UserUpdate, user: CurrentUser, repo: UserRepo) -> Any:
    """Update the current user's profile fields."""
    patch = body.model_dump(exclude_none=True)
    if not patch:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty patch body")

    if "username" in patch:
        taken = await repo.get_user_by_username(patch["username"])
        if taken is not None and taken["auth0_id"] != user.sub:
            raise HTTPException(status.HTTP_409_CONFLICT, "Username already taken")

    try:
        updated = await repo.update_user(user.sub, patch)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not registered")
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
    data = await repo.get_settings(user.sub)
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
    updated = await repo.update_settings(user.sub, patch)
    return updated
