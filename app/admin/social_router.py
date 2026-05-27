"""Admin moderation of social state — friend requests on behalf of any user.

Lives in its own router so we don't bloat ``app/admin/router.py`` and so the
endpoints can be re-mounted cleanly under any prefix. All routes gate on
``require_admin`` (env-driven allow-list + DB role); the target ``user_id``
in the path is the user whose request inbox/outbox is being moderated.

Endpoints
---------
``GET    /admin/social/users/{user_id}/friend-requests``
    Same shape as ``GET /social/friends/requests`` but for ``user_id``.

``POST   /admin/social/users/{user_id}/friend-requests/{requester_id}/accept``
    Accept the request from ``requester_id`` to ``user_id``.

``DELETE /admin/social/users/{user_id}/friend-requests/{other_id}``
    Decline / cancel a pending request in either direction.

The implementation mirrors the user-facing handlers in ``app/social/router.py``
but takes the operand id from the URL instead of the JWT.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.dependencies import require_admin
from app.auth.schemas import TokenPayload
from app.db.protocols import SocialRepository, UserRepository
from app.db.provider import get_social_repo, get_user_repo
from app.social.schemas import (
    FriendRequestItem,
    FriendRequestsResponse,
    FriendRequestStatus,
)

router = APIRouter(tags=["admin-social"])

AdminUser = Annotated[TokenPayload, Depends(require_admin)]
SocialRepo = Annotated[SocialRepository | None, Depends(get_social_repo)]
UserRepo = Annotated[UserRepository, Depends(get_user_repo)]


def _require_social(repo: SocialRepository | None) -> SocialRepository:
    if repo is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "social repository unavailable")
    return repo


def _user_to_request_item(user: dict[str, Any], when: str) -> FriendRequestItem:
    return FriendRequestItem(
        user_id=user["id"],
        username=user["username"],
        display_name=user["display_name"],
        requested_at=when,
    )


@router.get("/users/{user_id}/friend-requests", response_model=FriendRequestsResponse)
async def admin_list_friend_requests(
    user_id: str,
    _admin: AdminUser,
    social: SocialRepo,
    users: UserRepo,
) -> Any:
    """List incoming + outgoing friend requests for ``user_id``."""
    target = await users.get_user_by_id(user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    repo = _require_social(social)
    incoming_rows, outgoing_rows = await repo.list_friend_requests(user_id)
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


@router.post(
    "/users/{user_id}/friend-requests/{requester_id}/accept",
    response_model=FriendRequestStatus,
)
async def admin_accept_friend_request(
    user_id: str,
    requester_id: str,
    _admin: AdminUser,
    social: SocialRepo,
    users: UserRepo,
) -> Any:
    """Accept ``requester_id``→``user_id`` on the user's behalf."""
    target = await users.get_user_by_id(user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    requester = await users.get_user_by_id(requester_id)
    if requester is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Requester not found")
    repo = _require_social(social)
    req = await repo.get_friend_request(requester_id, user_id)
    if req is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No pending request from that user")
    await repo.add_friend_edge(user_id, requester_id)
    await repo.delete_friend_request(requester_id, user_id)
    await repo.delete_friend_request(user_id, requester_id)
    return FriendRequestStatus(status="accepted")


@router.delete(
    "/users/{user_id}/friend-requests/{other_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def admin_delete_friend_request(
    user_id: str,
    other_id: str,
    _admin: AdminUser,
    social: SocialRepo,
    users: UserRepo,
) -> None:
    """Cancel / decline a pending request in either direction for ``user_id``."""
    target = await users.get_user_by_id(user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    repo = _require_social(social)
    await repo.delete_friend_request(user_id, other_id)
    await repo.delete_friend_request(other_id, user_id)
