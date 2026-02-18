from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.dependencies import get_current_user
from app.auth.schemas import TokenPayload
from app.db.dependencies import get_user_repo
from app.db.protocols import UserRepository
from app.users.schemas import UserSettings, UserSettingsPatch

router = APIRouter(prefix="/api/core/users/v1", tags=["users"])

CurrentUser = Annotated[TokenPayload, Depends(get_current_user)]
UserRepo = Annotated[UserRepository, Depends(get_user_repo)]


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
