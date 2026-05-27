"""Admin API — user management. Requires admin role (default True for now)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.auth.dependencies import require_admin
from app.auth.schemas import TokenPayload
from app.db.protocols import (
    DeckRepository,
    SRSRepository,
    StoryRepository,
    SubscriptionRepository,
    UserRepository,
)
from app.db.provider import (
    get_deck_repo,
    get_srs_repo,
    get_story_repo,
    get_subscription_repo,
    get_user_repo,
)
from app.decks.router import _to_response
from app.decks.schemas import DeckResponse
from app.shared.errors import api_error
from app.shared.repos import require_repo
from app.srs.schemas import SRSCardState, SRSStateResponse
from app.stories.schemas import StoryResponse
from app.users.schemas import SubscriptionCreate, SubscriptionItem, UserResponse, UserUpdate
from app.users.subscriptions.content_types.registry import get_content_type_handler
from app.users.subscriptions.types import ContentType

router = APIRouter(tags=["admin"])

AdminUser = Annotated[TokenPayload, Depends(require_admin)]
UserRepo = Annotated[UserRepository | None, Depends(get_user_repo)]
SubRepo = Annotated[SubscriptionRepository | None, Depends(get_subscription_repo)]
DeckRepo = Annotated[DeckRepository | None, Depends(get_deck_repo)]
StoryRepo = Annotated[StoryRepository | None, Depends(get_story_repo)]
SRSRepo = Annotated[SRSRepository, Depends(get_srs_repo)]


# ── User list ─────────────────────────────────────────────────


@router.get("/users")
async def list_users(
    _admin: AdminUser,
    repo: UserRepo,
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = Query(None),
    search: str | None = Query(None, max_length=100),
    status: str | None = Query(None, max_length=32),
    community_status: str | None = Query(None, max_length=32),
    sort: str = Query("created_at", pattern=r"^(created_at|last_active_date|xp)$"),
    order: str = Query("desc", pattern=r"^(asc|desc)$"),
) -> dict[str, Any]:
    """List users for admin. Returns {items, nextCursor}.

    Filters: search (substring on username/display_name), status, community_status.
    Sort: ``created_at`` (default) | ``last_active_date`` | ``xp``.
    """
    r = require_repo(repo, "user")
    with api_error("listing users"):
        items, next_cursor = await r.list_users(
            limit=limit,
            cursor=cursor,
            search=search,
            status=status,
            community_status=community_status,
            sort=sort,
            order=order,
        )
    return {
        "items": [UserResponse(**u) for u in items],
        "nextCursor": next_cursor,
    }


@router.get("/stats/users")
async def get_user_stats(
    _admin: AdminUser,
    repo: UserRepo,
    since_days: int = Query(7, ge=1, le=365),
) -> dict[str, int]:
    """Aggregate user counts for the admin home dashboard.

    Returns total users, plus the number created and the number active
    within the last ``since_days`` window (default 7).
    """
    r = require_repo(repo, "user")
    with api_error("computing user stats"):
        stats = await r.user_stats(since_days=since_days)
    return {**stats, "since_days": since_days}


# ── User detail ───────────────────────────────────────────────


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: str,
    _admin: AdminUser,
    repo: UserRepo,
) -> Any:
    """Get user by ID (admin view)."""
    r = require_repo(repo, "user")
    with api_error("fetching user"):
        record = await r.get_user_by_id(user_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return UserResponse(**record)


@router.patch("/users/{user_id}", response_model=UserResponse)
async def admin_update_user(
    user_id: str,
    body: UserUpdate,
    _admin: AdminUser,
    repo: UserRepo,
) -> Any:
    """Update a user's profile (username, display_name, profile_picture_key, status)."""
    r = require_repo(repo, "user")
    with api_error("updating user"):
        record = await r.get_user_by_id(user_id)
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

        patch = body.model_dump(exclude_none=True)
        if not patch:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty patch body")

        if "username" in patch:
            taken = await r.get_user_by_username(patch["username"])
            if taken is not None and taken["id"] != user_id:
                raise HTTPException(status.HTTP_409_CONFLICT, "Username already taken")

        # Only admins can change roles; cannot demote self below admin
        if "role" in patch:
            from app.auth.roles import Role

            new_role = patch["role"]
            if new_role not in {role.value for role in Role}:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid role: {new_role}")
            if _admin.id == user_id and new_role not in ("admin", "super_admin"):
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    "Cannot demote your own admin role",
                )

        try:
            updated = await r.update_user(user_id, patch)
        except LookupError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found") from exc
    return UserResponse(**updated)


@router.get("/users/{user_id}/subscriptions", response_model=list[SubscriptionItem])
async def get_user_subscriptions(
    user_id: str,
    _admin: AdminUser,
    repo: UserRepo,
    sub_repo: SubRepo,
    content_type: str | None = Query(None),
) -> Any:
    """Get a user's subscriptions (admin)."""
    require_repo(repo, "user")
    r = require_repo(sub_repo, "subscription")
    with api_error("listing user subscriptions"):
        items = await r.list(user_id, content_type=content_type)
    return [SubscriptionItem(**x) for x in items]


@router.get("/users/{user_id}/content", response_model=list[DeckResponse])
async def get_user_content(
    user_id: str,
    _admin: AdminUser,
    repo: UserRepo,
    deck_repo: DeckRepo,
) -> Any:
    """Get decks authored by this user (admin). Excludes personal vocab decks."""
    require_repo(repo, "user")
    deck_r = require_repo(deck_repo, "deck")
    with api_error("listing user authored decks"):
        manifests = await deck_r.list_manifests(author_id=user_id)
        result = []
        for m in manifests:
            deck_id = m.get("id", "")
            if deck_id.startswith("vocab-"):
                continue
            deck = await deck_r.get_deck(deck_id)
            if deck:
                result.append(_to_response(deck, deck.get("cards", [])))
    return result


@router.get("/users/{user_id}/srs", response_model=SRSStateResponse)
async def admin_get_user_srs(
    user_id: str,
    _admin: AdminUser,
    repo: UserRepo,
    srs_repo: SRSRepo,
) -> Any:
    """Get SRS state for a user (admin)."""
    r = require_repo(repo, "user")
    with api_error("fetching user SRS state"):
        existing = await r.get_user_by_id(user_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="User not found")
        cards = await srs_repo.get_all(user_id)
    return {"cards": cards}


class AdminSRSPatchRequest(BaseModel):
    """Admin update of SRS state. Keys are card IDs."""

    cards: dict[str, SRSCardState]


@router.patch("/users/{user_id}/srs", response_model=SRSStateResponse)
async def admin_update_user_srs(
    user_id: str,
    body: AdminSRSPatchRequest,
    _admin: AdminUser,
    repo: UserRepo,
    srs_repo: SRSRepo,
) -> Any:
    """Update SRS state for a user (admin). Merges provided cards into existing state."""
    r = require_repo(repo, "user")
    with api_error("updating user SRS state"):
        existing = await r.get_user_by_id(user_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="User not found")
        if not body.cards:
            cards = await srs_repo.get_all(user_id)
            return {"cards": cards}
        cards_dict = {cid: s.model_dump(mode="json") for cid, s in body.cards.items()}
        merged = await srs_repo.upsert_cards(user_id, cards_dict)
    return {"cards": merged}


class AdminSRSDeleteRequest(BaseModel):
    cardIds: list[str]


@router.delete("/users/{user_id}/srs/cards")
async def admin_delete_user_srs_cards(
    user_id: str,
    body: AdminSRSDeleteRequest,
    _admin: AdminUser,
    repo: UserRepo,
    srs_repo: SRSRepo,
) -> dict:
    """Delete SRS state for specific cards (admin)."""
    r = require_repo(repo, "user")
    with api_error("deleting user SRS cards"):
        existing = await r.get_user_by_id(user_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="User not found")
        count = await srs_repo.delete_cards(user_id, body.cardIds)
    return {"deleted": count}


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
    r = require_repo(repo, "user")
    with api_error("deleting user"):
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
    story_repo: StoryRepo,
) -> Any:
    """Add a subscription for a user (admin)."""
    require_repo(repo, "user")
    r = require_repo(sub_repo, "subscription")
    if body.contentType not in [c.value for c in ContentType]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"contentType must be one of: {[c.value for c in ContentType]}",
        )
    handler = get_content_type_handler(
        body.contentType,
        context={"deck_repo": deck_repo, "story_repo": story_repo},
    )
    with api_error("adding user subscription"):
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
    require_repo(repo, "user")
    r = require_repo(sub_repo, "subscription")
    if content_type not in [c.value for c in ContentType]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"contentType must be one of: {[c.value for c in ContentType]}",
        )
    with api_error("removing user subscription"):
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
    r = require_repo(deck_repo, "deck")
    with api_error("updating deck status"):
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
    r = require_repo(deck_repo, "deck")
    with api_error("deleting deck"):
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
    r = require_repo(story_repo, "story")
    with api_error("listing admin stories"):
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
    r = require_repo(story_repo, "story")
    with api_error("updating story status"):
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
    r = require_repo(story_repo, "story")
    with api_error("deleting story"):
        existing = await r.get_story(story_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Story not found")
        await r.delete_story(story_id)
