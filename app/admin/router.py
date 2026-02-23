"""Admin API — user management. Requires admin role (default True for now)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.dependencies import require_admin
from app.auth.schemas import TokenPayload
from app.db.provider import get_deck_repo, get_subscription_repo, get_user_repo
from app.db.protocols import DeckRepository, SubscriptionRepository, UserRepository
from app.decks.router import _to_response
from app.decks.schemas import DeckResponse
from app.users.schemas import SubscriptionItem, UserResponse

router = APIRouter(tags=["admin"])

AdminUser = Annotated[TokenPayload, Depends(require_admin)]
UserRepo = Annotated[UserRepository | None, Depends(get_user_repo)]
SubRepo = Annotated[SubscriptionRepository | None, Depends(get_subscription_repo)]
DeckRepo = Annotated[DeckRepository | None, Depends(get_deck_repo)]


def _require_user_repo(repo: UserRepository | None) -> UserRepository:
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="User storage not configured",
        )
    return repo


def _require_sub_repo(repo: SubscriptionRepository | None) -> SubscriptionRepository:
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Subscription storage not configured",
        )
    return repo


def _require_deck_repo(repo: DeckRepository | None) -> DeckRepository:
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Deck storage not configured",
        )
    return repo


# ── User list ─────────────────────────────────────────────────


@router.get("/users")
async def list_users(
    _admin: AdminUser,
    repo: UserRepo,
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = Query(None),
) -> dict[str, Any]:
    """List users for admin. Returns {items, nextCursor}."""
    r = _require_user_repo(repo)
    items, next_cursor = await r.list_users(limit=limit, cursor=cursor)
    return {
        "items": [UserResponse(**u) for u in items],
        "nextCursor": next_cursor,
    }


# ── User detail ───────────────────────────────────────────────


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: str,
    _admin: AdminUser,
    repo: UserRepo,
) -> Any:
    """Get user by ID (admin view)."""
    r = _require_user_repo(repo)
    record = await r.get_user_by_id(user_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return UserResponse(**record)


@router.get("/users/{user_id}/subscriptions", response_model=list[SubscriptionItem])
async def get_user_subscriptions(
    user_id: str,
    _admin: AdminUser,
    repo: UserRepo,
    sub_repo: SubRepo,
    content_type: str | None = Query(None),
) -> Any:
    """Get a user's subscriptions (admin)."""
    _require_user_repo(repo)
    r = _require_sub_repo(sub_repo)
    items = await r.list(user_id, content_type=content_type)
    return [SubscriptionItem(**x) for x in items]


@router.get("/users/{user_id}/content", response_model=list[DeckResponse])
async def get_user_content(
    user_id: str,
    _admin: AdminUser,
    repo: UserRepo,
    deck_repo: DeckRepo,
) -> Any:
    """Get decks authored by this user (admin)."""
    _require_user_repo(repo)
    deck_r = _require_deck_repo(deck_repo)
    manifests = await deck_r.list_manifests(author_id=user_id)
    result = []
    for m in manifests:
        deck = await deck_r.get_deck(m["id"])
        if deck:
            result.append(_to_response(deck, deck.get("cards", [])))
    return result


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str,
    _admin: AdminUser,
    repo: UserRepo,
) -> None:
    """Delete a user and their settings. Cannot delete self."""
    if _admin.id == user_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Cannot delete your own account",
        )
    r = _require_user_repo(repo)
    existing = await r.get_user_by_id(user_id)
    if existing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    await r.delete_user(user_id)
