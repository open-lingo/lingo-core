"""Admin impersonation — start / stop endpoints.

The actual impersonation is request-header driven (``X-Impersonate-User-Id``)
and recognized in ``app.auth.dependencies.get_acting_user``. These two
endpoints exist for:

  - **start**: validate the target user up front, return their public
    fields so the FE can render the "Acting as @<username>" banner
    without a follow-up roundtrip. Audit-logs ``impersonate_start``.
  - **stop**: audit-log ``impersonate_stop`` when the admin clicks the
    banner's Stop button. The FE clears its sessionStorage state on
    success.

Neither endpoint mints a session token or sets a cookie — the admin's
Auth0 JWT continues to be the identity proven to the BE, the
``X-Impersonate-User-Id`` header is the override-on-top. This keeps
Auth0 untouched (see PR notes — the SSO impersonation path is a
deferred follow-up).
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.admin.audit_router import record_admin_action
from app.auth.dependencies import require_admin
from app.auth.schemas import TokenPayload
from app.db.protocols import UserRepository
from app.db.provider import get_user_repo
from app.shared.errors import api_error

router = APIRouter(tags=["admin", "impersonate"])

AdminUser = Annotated[TokenPayload, Depends(require_admin)]
UserRepo = Annotated[UserRepository, Depends(get_user_repo)]


class ImpersonateStartResponse(BaseModel):
    ok: bool
    target_user_id: str
    target_username: str
    target_display_name: str


@router.post("/impersonate/{user_id}/start", response_model=ImpersonateStartResponse)
async def impersonate_start(
    user_id: str,
    admin: AdminUser,
    users: UserRepo,
) -> Any:
    """Validate target + return their public fields for the FE banner.

    The admin can ALREADY impersonate by setting the header — this
    endpoint just confirms the target exists and records the
    ``impersonate_start`` audit entry so the trail has a session anchor.
    """
    with api_error("starting impersonation"):
        target = await users.get_user_by_id(user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "target user not found")

    await record_admin_action(
        actor_id=admin.id,
        action="impersonate_start",
        target_id=user_id,
        target_kind="user",
        payload={
            "target_username": target.get("username"),
            "actor_sub": admin.sub,
        },
    )
    return ImpersonateStartResponse(
        ok=True,
        target_user_id=target["id"],
        target_username=target.get("username") or "",
        target_display_name=target.get("display_name") or "",
    )


@router.post("/impersonate/stop", status_code=status.HTTP_204_NO_CONTENT)
async def impersonate_stop(admin: AdminUser) -> None:
    """Audit-log that the admin stopped impersonating. FE clears its
    sessionStorage state on a 204 response."""
    await record_admin_action(
        actor_id=admin.id,
        action="impersonate_stop",
        target_id=None,
        target_kind="user",
        payload={"actor_sub": admin.sub},
    )
    return None
