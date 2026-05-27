"""Community/forum API endpoints.

Uses CommunityRepository (mock for now). Markdown stored as text; React markdown editor compatible.
"""

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.dependencies import get_community_user_optional
from app.auth.schemas import TokenPayload
from app.community.schemas import (
    AddonCreate,
    AddonPatch,
    AddonResponse,
    CategoryResponse,
    ContentLinkResponse,
    DeckContentStore,
    MarkdownResponse,
    MarkdownStoreRequest,
    PostCreate,
    PostPatch,
    PostResponse,
    TagCreate,
    TagResponse,
    ThreadCreate,
    ThreadPatch,
    ThreadResponse,
    VoteRequest,
)
from app.db.protocols import CommunityRepository
from app.db.provider import get_community_repo
from app.shared.errors import api_error

router = APIRouter(tags=["community"])

CommunityRepo = Annotated[CommunityRepository, Depends(get_community_repo)]
CurrentUser = Annotated[TokenPayload | None, Depends(get_community_user_optional)]


def _thread_to_response(thread: dict, tag_ids: list[str], content_links: list[dict]) -> ThreadResponse:
    return ThreadResponse(
        id=thread["id"],
        category_id=thread["category_id"],
        author_id=thread["author_id"],
        author_name=thread.get("author_name", "User"),
        title=thread["title"],
        excerpt=thread.get("excerpt", ""),
        body_markdown=thread.get("body_markdown", ""),
        reply_count=thread.get("reply_count", 0),
        upvote_count=thread.get("upvote_count", 0),
        downvote_count=thread.get("downvote_count", 0),
        view_count=thread.get("view_count", 0),
        is_pinned=thread.get("is_pinned", False),
        status=thread.get("status", "open"),
        tag_ids=tag_ids,
        content_links=[ContentLinkResponse(**cl) for cl in content_links],
        created_at=thread["created_at"],
        updated_at=thread["updated_at"],
    )


# ── Categories ──


@router.get("/categories", response_model=list[CategoryResponse])
async def list_categories(repo: CommunityRepo) -> Any:
    """List all forum categories."""
    with api_error("listing categories"):
        items = await repo.list_categories()
    return items


@router.get("/categories/{category_id}", response_model=CategoryResponse)
async def get_category(category_id: str, repo: CommunityRepo) -> Any:
    """Get a category by id."""
    with api_error("fetching category"):
        cat = await repo.get_category_by_id(category_id)
    if not cat:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Category not found")
    return cat


# ── Tags ──


@router.get("/tags", response_model=list[TagResponse])
async def list_tags(repo: CommunityRepo) -> Any:
    """List all forum tags."""
    with api_error("listing tags"):
        items = await repo.list_tags()
    return items


@router.post("/tags", response_model=TagResponse, status_code=status.HTTP_201_CREATED)
async def create_tag(
    body: TagCreate,
    repo: CommunityRepo,
    user: CurrentUser,
) -> Any:
    """Create a tag (auth required for future moderation)."""
    with api_error("creating tag"):
        tag = await repo.create_tag(body.model_dump())
    return tag


# ── Threads ──


@router.get("/threads", response_model=list[ThreadResponse])
async def list_threads(
    repo: CommunityRepo,
    category_id: str | None = None,
    tag_id: str | None = None,
    content_type: str | None = None,
    content_id: str | None = None,
    sort: str = "hot",
    limit: int = 50,
    offset: int = 0,
) -> Any:
    """List threads with optional filters."""
    with api_error("listing threads"):
        items = await repo.list_threads(
            category_id=category_id,
            tag_id=tag_id,
            content_type=content_type,
            content_id=content_id,
            sort=sort,
            limit=min(limit, 100),
            offset=offset,
        )
        result = []
        for t in items:
            tag_ids = await repo.get_thread_tag_ids(t["id"])
            links = await repo.list_content_links_by_thread(t["id"])
            result.append(_thread_to_response(t, tag_ids, links))
    return result


@router.post("/threads", response_model=ThreadResponse, status_code=status.HTTP_201_CREATED)
async def create_thread(
    body: ThreadCreate,
    repo: CommunityRepo,
    user: CurrentUser,
) -> Any:
    """Create a new thread. Auth required."""
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    with api_error("creating thread"):
        thread = await repo.create_thread(
            {
                "category_id": body.category_id,
                "author_id": user.sub,
                "author_name": user.sub,  # TODO: resolve from user repo
                "title": body.title,
                "excerpt": body.excerpt or body.title[:200],
                "body_markdown": body.body_markdown,
                "tag_ids": body.tag_ids,
            }
        )
        await repo.set_thread_tags(thread["id"], body.tag_ids)
        for cl in body.content_links:
            await repo.add_content_link(
                thread["id"],
                cl.content_type,
                cl.content_id,
                cl.language_id,
            )
        tag_ids = await repo.get_thread_tag_ids(thread["id"])
        links = await repo.list_content_links_by_thread(thread["id"])
    return _thread_to_response(thread, tag_ids, links)


@router.get("/threads/{thread_id}", response_model=ThreadResponse)
async def get_thread(thread_id: str, repo: CommunityRepo) -> Any:
    """Get a thread by id. Increments view count."""
    with api_error("fetching thread"):
        thread = await repo.get_thread_by_id(thread_id)
        if not thread:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found")
        await repo.increment_thread_views(thread_id)
        thread["view_count"] = thread.get("view_count", 0) + 1
        tag_ids = await repo.get_thread_tag_ids(thread_id)
        links = await repo.list_content_links_by_thread(thread_id)
    return _thread_to_response(thread, tag_ids, links)


@router.patch("/threads/{thread_id}", response_model=ThreadResponse)
async def update_thread(
    thread_id: str,
    body: ThreadPatch,
    repo: CommunityRepo,
    user: CurrentUser,
) -> Any:
    """Update a thread. Auth required (author or moderator)."""
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    patch = body.model_dump(exclude_none=True)
    if not patch:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty patch")
    with api_error("updating thread"):
        try:
            thread = await repo.update_thread(thread_id, patch)
        except LookupError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found") from exc
        tag_ids = await repo.get_thread_tag_ids(thread_id)
        links = await repo.list_content_links_by_thread(thread_id)
    return _thread_to_response(thread, tag_ids, links)


@router.post("/threads/{thread_id}/vote")
async def vote_thread(
    thread_id: str,
    body: VoteRequest,
    repo: CommunityRepo,
    user: CurrentUser,
) -> dict[str, str]:
    """Upvote (1) or downvote (-1) a thread. Auth required."""
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    if body.value not in (1, -1):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "value must be 1 or -1")
    with api_error("voting on thread"):
        await repo.upsert_vote(user.sub, "thread", thread_id, body.value)
    return {"status": "ok"}


@router.delete("/threads/{thread_id}/vote")
async def remove_thread_vote(
    thread_id: str,
    repo: CommunityRepo,
    user: CurrentUser,
) -> dict[str, str]:
    """Remove vote from thread."""
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    with api_error("removing thread vote"):
        await repo.remove_vote(user.sub, "thread", thread_id)
    return {"status": "ok"}


# ── Posts ──


@router.get("/threads/{thread_id}/posts", response_model=list[PostResponse])
async def list_posts(
    thread_id: str,
    repo: CommunityRepo,
    limit: int = 100,
    offset: int = 0,
) -> Any:
    """List posts (replies) for a thread."""
    with api_error("listing posts"):
        items = await repo.list_posts_by_thread(thread_id, limit=min(limit, 200), offset=offset)
    return items


@router.post(
    "/threads/{thread_id}/posts",
    response_model=PostResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_post(
    thread_id: str,
    body: PostCreate,
    repo: CommunityRepo,
    user: CurrentUser,
) -> Any:
    """Create a reply in a thread. Auth required."""
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    with api_error("creating post"):
        thread = await repo.get_thread_by_id(thread_id)
        if not thread:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found")
        post = await repo.create_post(
            {
                "thread_id": thread_id,
                "parent_id": body.parent_id,
                "author_id": user.sub,
                "author_name": user.sub,
                "body_markdown": body.body_markdown,
            }
        )
    return post


@router.patch("/posts/{post_id}", response_model=PostResponse)
async def update_post(
    post_id: str,
    body: PostPatch,
    repo: CommunityRepo,
    user: CurrentUser,
) -> Any:
    """Update a post. Auth required."""
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    patch = body.model_dump(exclude_none=True)
    if not patch:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty patch")
    with api_error("updating post"):
        try:
            return await repo.update_post(post_id, patch)
        except LookupError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Post not found") from exc


@router.post("/posts/{post_id}/vote")
async def vote_post(
    post_id: str,
    body: VoteRequest,
    repo: CommunityRepo,
    user: CurrentUser,
) -> dict[str, str]:
    """Upvote or downvote a post."""
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    if body.value not in (1, -1):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "value must be 1 or -1")
    with api_error("voting on post"):
        await repo.upsert_vote(user.sub, "post", post_id, body.value)
    return {"status": "ok"}


# ── Content links (threads by content) ──


@router.get("/content/{content_type}/{content_id}/threads", response_model=list[ThreadResponse])
async def list_threads_by_content(
    content_type: str,
    content_id: str,
    repo: CommunityRepo,
    limit: int = 50,
) -> Any:
    """List threads linked to specific content (e.g. official_course/official-ko)."""
    with api_error("listing threads by content"):
        items = await repo.list_threads_by_content(content_type, content_id, limit=limit)
        result = []
        for t in items:
            tag_ids = await repo.get_thread_tag_ids(t["id"])
            links = await repo.list_content_links_by_thread(t["id"])
            result.append(_thread_to_response(t, tag_ids, links))
    return result


# ── Addons ──


@router.get("/addons", response_model=list[AddonResponse])
async def list_addons(
    repo: CommunityRepo,
    kind: str | None = None,
    language_id: str | None = None,
    addon_status: str | None = None,
    author_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Any:
    """List community addons with optional filters.
    status: filter by draft|published (omit = all).
    author_id: filter by author (for 'My Content').
    """
    with api_error("listing addons"):
        items = await repo.list_addons(
            kind=kind,
            language_id=language_id,
            status=addon_status,
            author_id=author_id,
            limit=limit,
            offset=offset,
        )
    return items


@router.post("/addons", response_model=AddonResponse, status_code=status.HTTP_201_CREATED)
async def create_addon(
    body: AddonCreate,
    repo: CommunityRepo,
    user: CurrentUser,
) -> Any:
    """Create a community addon. Auth required."""
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    with api_error("creating addon"):
        addon = await repo.create_addon(
            {
                **body.model_dump(),
                "author_id": user.sub,
            }
        )
    return addon


@router.get("/addons/{addon_id}", response_model=AddonResponse)
async def get_addon(addon_id: str, repo: CommunityRepo) -> Any:
    """Get an addon by id."""
    with api_error("fetching addon"):
        addon = await repo.get_addon_by_id(addon_id)
    if not addon:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Addon not found")
    return addon


@router.patch("/addons/{addon_id}", response_model=AddonResponse)
async def update_addon(
    addon_id: str,
    body: AddonPatch,
    repo: CommunityRepo,
    user: CurrentUser,
) -> Any:
    """Update addon metadata. Auth required; only author can update."""
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    with api_error("updating addon"):
        addon = await repo.get_addon_by_id(addon_id)
        if not addon:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Addon not found")
        if addon.get("author_id") != user.sub:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the author can update this addon")
        patch = body.model_dump(exclude_unset=True)
        updated = await repo.update_addon(addon_id, patch)
    return updated


@router.put("/addons/{addon_id}/deck", response_model=dict)
async def put_addon_deck(
    addon_id: str,
    body: DeckContentStore,
    repo: CommunityRepo,
    user: CurrentUser,
) -> Any:
    """Store deck content (cards) for a flashcard_pack addon. Auth required."""
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    with api_error("storing addon deck content"):
        addon = await repo.get_addon_by_id(addon_id)
        if not addon:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Addon not found")
        if addon.get("author_id") != user.sub:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the author can edit this deck")
        if addon.get("kind") != "flashcard_pack":
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Deck content is only for flashcard_pack addons",
            )
        key = f"addons/{addon_id}/deck"
        content = json.dumps({"cards": body.cards})
        await repo.store_markdown(key, content, content_type="application/json")
        await repo.update_addon(addon_id, {"item_count": len(body.cards)})
    return {"ok": True, "card_count": len(body.cards)}


@router.get("/addons/{addon_id}/deck")
async def get_addon_deck(
    addon_id: str,
    repo: CommunityRepo,
) -> Any:
    """Get deck content (cards) for a flashcard_pack addon."""
    with api_error("fetching addon deck content"):
        addon = await repo.get_addon_by_id(addon_id)
        if not addon:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Addon not found")
        key = f"addons/{addon_id}/deck"
        stored = await repo.get_markdown(key)
    if not stored:
        return {"cards": []}
    try:
        data = json.loads(stored["content"])
        return {"cards": data.get("cards", [])}
    except json.JSONDecodeError:
        return {"cards": []}


# ── Markdown storage (for rich content, React markdown editor compatibility) ──


@router.put("/markdown", response_model=MarkdownResponse)
async def store_markdown(
    body: MarkdownStoreRequest,
    repo: CommunityRepo,
    user: CurrentUser,
) -> Any:
    """Store markdown content by key. Compatible with React markdown editor.
    Key can be path-like (e.g. addons/abc123/readme)."""
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    with api_error("storing markdown"):
        stored = await repo.store_markdown(
            body.key,
            body.content,
            content_type=body.content_type,
            metadata=body.metadata,
        )
    return stored


@router.get("/markdown/{key:path}", response_model=MarkdownResponse)
async def get_markdown(key: str, repo: CommunityRepo) -> Any:
    """Retrieve markdown by key."""
    with api_error("fetching markdown"):
        m = await repo.get_markdown(key)
    if not m:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Markdown not found")
    return m


@router.delete("/markdown/{key:path}")
async def delete_markdown(
    key: str,
    repo: CommunityRepo,
    user: CurrentUser,
) -> dict[str, bool]:
    """Delete markdown by key."""
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    with api_error("deleting markdown"):
        deleted = await repo.delete_markdown(key)
    return {"deleted": deleted}
