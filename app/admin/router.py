"""Admin API — user management. Requires admin role (default True for now)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.dependencies import require_admin
from app.auth.schemas import TokenPayload
from app.db.provider import get_deck_repo, get_story_repo, get_subscription_repo, get_user_repo
from app.db.protocols import DeckRepository, StoryRepository, SubscriptionRepository, UserRepository
from app.decks.router import _to_response
from app.decks.schemas import DeckResponse
from app.stories.schemas import StoryResponse
from app.users.schemas import SubscriptionCreate, SubscriptionItem, UserResponse
from app.users.subscriptions.content_types.registry import get_content_type_handler
from app.users.subscriptions.types import ContentType

router = APIRouter(tags=["admin"])

AdminUser = Annotated[TokenPayload, Depends(require_admin)]
UserRepo = Annotated[UserRepository | None, Depends(get_user_repo)]
SubRepo = Annotated[SubscriptionRepository | None, Depends(get_subscription_repo)]
DeckRepo = Annotated[DeckRepository | None, Depends(get_deck_repo)]
StoryRepo = Annotated[StoryRepository | None, Depends(get_story_repo)]


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


def _require_story_repo(repo: StoryRepository | None) -> StoryRepository:
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Story storage not configured",
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


# ── Admin: user subscriptions ─────────────────────────────────────


@router.post(
    "/users/{user_id}/subscriptions",
    response_model=SubscriptionItem,
    status_code=status.HTTP_201_CREATED,
)
async def admin_add_subscription(
    user_id: str,
    body: SubscriptionCreate,
    _admin: AdminUser,
    repo: UserRepo,
    sub_repo: SubRepo,
    deck_repo: DeckRepo,
) -> Any:
    """Add a subscription for a user (admin)."""
    _require_user_repo(repo)
    r = _require_sub_repo(sub_repo)
    if body.contentType not in [c.value for c in ContentType]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"contentType must be one of: {[c.value for c in ContentType]}",
        )
    handler = get_content_type_handler(body.contentType, context={"deck_repo": deck_repo})
    if not await handler.validate_subscription(body.contentId):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{body.contentType} not found: {body.contentId}",
        )
    await r.add(user_id, body.contentType, body.contentId)
    items = await r.list(user_id, content_type=body.contentType)
    added = next((i for i in items if i["contentId"] == body.contentId), None)
    if not added:
        raise HTTPException(status_code=500, detail="Subscription add failed")
    return SubscriptionItem(**added)


@router.delete(
    "/users/{user_id}/subscriptions/{content_type}/{content_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def admin_remove_subscription(
    user_id: str,
    content_type: str,
    content_id: str,
    _admin: AdminUser,
    repo: UserRepo,
    sub_repo: SubRepo,
) -> None:
    """Remove a subscription for a user (admin)."""
    _require_user_repo(repo)
    r = _require_sub_repo(sub_repo)
    if content_type not in [c.value for c in ContentType]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"contentType must be one of: {[c.value for c in ContentType]}",
        )
    await r.remove(user_id, content_type, content_id)


# ── Admin: deck status and delete ──────────────────────────────────


@router.patch("/decks/{deck_id}/status", response_model=DeckResponse)
async def admin_update_deck_status(
    deck_id: str,
    _admin: AdminUser,
    deck_repo: DeckRepo,
    status_param: str = Query(..., alias="status", description="draft | published"),
) -> Any:
    """Unpublish (draft) or publish a deck. Admin only."""
    if status_param not in ("draft", "published"):
        raise HTTPException(status_code=400, detail="status must be draft or published")
    r = _require_deck_repo(deck_repo)
    existing = await r.get_deck(deck_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Deck not found")
    manifest = {**existing, "status": status_param, "authorId": existing.get("authorId")}
    await r.upsert_deck(deck_id, manifest, existing.get("cards", []))
    deck = await r.get_deck(deck_id)
    if not deck:
        raise HTTPException(status_code=500, detail="Deck update failed")
    return _to_response(deck, deck.get("cards", []))


@router.delete("/decks/{deck_id}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_delete_deck(
    deck_id: str,
    _admin: AdminUser,
    deck_repo: DeckRepo,
) -> None:
    """Delete a deck permanently. Admin only."""
    r = _require_deck_repo(deck_repo)
    existing = await r.get_deck(deck_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Deck not found")
    await r.delete_deck(deck_id)


# ── Admin: story list, status, delete ─────────────────────────────────


def _story_to_response(story: dict) -> StoryResponse:
    return StoryResponse(
        id=story["id"],
        languageId=story.get("languageId", ""),
        title=story.get("title", ""),
        description=story.get("description"),
        companionDeckId=story.get("companionDeckId", ""),
        body=story.get("body", ""),
        authorId=story.get("authorId"),
        status=story.get("status", "draft"),
        createdAt=story.get("createdAt"),
        updatedAt=story.get("updatedAt"),
    )


@router.get("/stories", response_model=list[StoryResponse])
async def list_admin_stories(
    _admin: AdminUser,
    story_repo: StoryRepo,
    status: str | None = Query(None, description="draft | published"),
    language_id: str | None = Query(None),
) -> Any:
    """List all stories for admin. Optional filters."""
    r = _require_story_repo(story_repo)
    stories = await r.list_stories(author_id=None, language_id=language_id, status=status)
    return [_story_to_response(s) for s in stories]


@router.patch("/stories/{story_id}/status", response_model=StoryResponse)
async def admin_update_story_status(
    story_id: str,
    _admin: AdminUser,
    story_repo: StoryRepo,
    status_param: str = Query(..., alias="status", description="draft | published"),
) -> Any:
    """Unpublish (draft) or publish a story. Admin only."""
    if status_param not in ("draft", "published"):
        raise HTTPException(status_code=400, detail="status must be draft or published")
    r = _require_story_repo(story_repo)
    existing = await r.get_story(story_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Story not found")
    await r.update_story(story_id, {"status": status_param})
    story = await r.get_story(story_id)
    if not story:
        raise HTTPException(status_code=500, detail="Story update failed")
    return _story_to_response(story)


@router.delete("/stories/{story_id}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_delete_story(
    story_id: str,
    _admin: AdminUser,
    story_repo: StoryRepo,
) -> None:
    """Delete a story permanently. Admin only."""
    r = _require_story_repo(story_repo)
    existing = await r.get_story(story_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Story not found")
    await r.delete_story(story_id)
