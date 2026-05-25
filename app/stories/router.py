"""Story API for community content."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.dependencies import get_current_user
from app.auth.schemas import TokenPayload
from app.db.provider import get_story_repo
from app.db.protocols import StoryRepository
from app.shared.errors import api_error
from app.shared.repos import require_repo
from app.stories.schemas import StoryCreate, StoryResponse, StoryUpdate

router = APIRouter(tags=["stories"])

StoryRepo = Annotated[StoryRepository | None, Depends(get_story_repo)]
CurrentUser = Annotated[TokenPayload, Depends(get_current_user)]


def _to_response(story: dict) -> StoryResponse:
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


@router.get("/browse", response_model=list[StoryResponse])
async def list_browse_stories(
    repo: StoryRepo,
    user: CurrentUser,
    language_id: str | None = Query(None, description="Filter by language"),
) -> list[StoryResponse]:
    """List published stories for browsing. Any authenticated user."""
    r = require_repo(repo, "stories")
    with api_error("listing published stories"):
        stories = await r.list_stories(
            author_id=None,
            language_id=language_id,
            status="published",
        )
    return [_to_response(s) for s in stories]


@router.get("", response_model=list[StoryResponse])
async def list_my_stories(
    repo: StoryRepo,
    user: CurrentUser,
    language_id: str | None = Query(None, description="Filter by language"),
    story_status: str | None = Query(None, alias="status", description="Filter by status: draft, published"),
) -> list[StoryResponse]:
    """List stories owned by the current user."""
    r = require_repo(repo, "stories")
    with api_error("listing user stories"):
        stories = await r.list_stories(
            author_id=user.id,
            language_id=language_id,
            status=story_status,
        )
    return [_to_response(s) for s in stories]


@router.post("", response_model=StoryResponse, status_code=status.HTTP_201_CREATED)
async def create_story(
    body: StoryCreate,
    repo: StoryRepo,
    user: CurrentUser,
) -> StoryResponse:
    """Create a new story."""
    r = require_repo(repo, "stories")
    story_id = f"story-{uuid.uuid4().hex[:12]}"
    data = {
        "languageId": body.languageId,
        "title": body.title,
        "description": body.description or "",
        "companionDeckId": body.companionDeckId,
        "body": body.body or "",
        "authorId": user.id,
        "status": "draft",
    }
    with api_error("creating story"):
        await r.create_story(story_id, data)
        story = await r.get_story(story_id)
    if not story:
        raise HTTPException(status_code=500, detail="Story creation failed")
    return _to_response(story)


@router.get("/{story_id}", response_model=StoryResponse)
async def get_story(
    story_id: str,
    repo: StoryRepo,
    user: CurrentUser,
) -> StoryResponse:
    """Get a story by ID. User must own it (for drafts) or it must be published."""
    r = require_repo(repo, "stories")
    with api_error("fetching story"):
        story = await r.get_story(story_id)
    if not story:
        raise HTTPException(status_code=404, detail="Story not found")
    author = story.get("authorId")
    story_status = story.get("status", "published")
    if author and author != user.id and story_status == "draft":
        raise HTTPException(status_code=404, detail="Story not found")
    return _to_response(story)


@router.put("/{story_id}", response_model=StoryResponse)
async def update_story(
    story_id: str,
    body: StoryUpdate,
    repo: StoryRepo,
    user: CurrentUser,
) -> StoryResponse:
    """Update a story. User must be the author."""
    r = require_repo(repo, "stories")
    with api_error("fetching story"):
        existing = await r.get_story(story_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Story not found")
    if existing.get("authorId") and existing.get("authorId") != user.id:
        raise HTTPException(status_code=403, detail="Only the author can update this story")
    patch = body.model_dump(exclude_unset=True)
    with api_error("updating story"):
        await r.update_story(story_id, patch)
        story = await r.get_story(story_id)
    if not story:
        raise HTTPException(status_code=500, detail="Story update failed")
    return _to_response(story)


@router.patch("/{story_id}/status", response_model=StoryResponse)
async def update_story_status(
    story_id: str,
    repo: StoryRepo,
    user: CurrentUser,
    status: str = Query(..., description="draft | published"),
) -> StoryResponse:
    """Change story status. User must be the author."""
    if status not in ("draft", "published"):
        raise HTTPException(status_code=400, detail="status must be draft or published")
    r = require_repo(repo, "stories")
    with api_error("fetching story"):
        existing = await r.get_story(story_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Story not found")
    if existing.get("authorId") and existing.get("authorId") != user.id:
        raise HTTPException(status_code=403, detail="Only the author can update this story")
    with api_error("updating story status"):
        await r.update_story(story_id, {"status": status})
        story = await r.get_story(story_id)
    if not story:
        raise HTTPException(status_code=500, detail="Story update failed")
    return _to_response(story)
