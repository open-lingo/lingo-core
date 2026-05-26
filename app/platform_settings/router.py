"""Admin-tunable platform settings (XP economy today).

Mounted under ``/api/core/v1/admin/platform-settings``. All routes require
admin. Reads return either the stored blob or the schema defaults so the
admin UI always renders something.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.auth.dependencies import require_admin
from app.auth.schemas import TokenPayload
from app.db.protocols import PlatformSettingsRepository
from app.db.provider import get_platform_settings_repo
from app.platform_settings.schemas import XP_ECONOMY_KEY, XpEconomyConfig
from app.shared.errors import api_error

router = APIRouter(tags=["admin", "platform-settings"])

AdminUser = Annotated[TokenPayload, Depends(require_admin)]
PlatformSettingsRepo = Annotated[
    PlatformSettingsRepository | None, Depends(get_platform_settings_repo)
]


@router.get("/xp", response_model=XpEconomyConfig)
async def get_xp_economy(
    _admin: AdminUser,
    repo: PlatformSettingsRepo,
) -> Any:
    """Return the current XP economy config, filling in schema defaults
    for any keys that aren't present in storage yet."""
    stored: dict[str, Any] = {}
    if repo is not None:
        with api_error("reading platform settings"):
            stored = await repo.get(XP_ECONOMY_KEY) or {}
    # Pydantic merges in defaults for missing keys.
    return XpEconomyConfig(**stored)


@router.put("/xp", response_model=XpEconomyConfig)
async def put_xp_economy(
    body: XpEconomyConfig,
    _admin: AdminUser,
    repo: PlatformSettingsRepo,
) -> Any:
    """Replace the XP economy config. Body must validate against the
    ``XpEconomyConfig`` schema."""
    if repo is None:
        # Without storage this PUT can't be durable; surface the degraded
        # state instead of silently dropping the write.
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="platform_settings storage is unavailable",
        )
    with api_error("writing platform settings"):
        await repo.put(XP_ECONOMY_KEY, body.model_dump())
    return body
