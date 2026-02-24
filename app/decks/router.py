"""Deck API for community content.

Saves directly to the main deck database. Auth required for create/update.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.dependencies import get_current_user, get_registered_user
from app.auth.schemas import TokenPayload
from app.db.provider import get_deck_repo, get_subscription_repo
from app.db.protocols import DeckRepository, SubscriptionRepository
from app.decks.schemas import AddCardsRequest, DeckCreate, DeckResponse, DeckUpdate

router = APIRouter(tags=["decks"])

DeckRepo = Annotated[DeckRepository | None, Depends(get_deck_repo)]
SubRepo = Annotated[SubscriptionRepository | None, Depends(get_subscription_repo)]
CurrentUser = Annotated[TokenPayload, Depends(get_current_user)]
RegisteredUser = Annotated[TokenPayload, Depends(get_registered_user)]


def _require_deck_repo(repo: DeckRepository | None) -> DeckRepository:
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Deck storage not configured",
        )
    return repo


def _manifest_from_body(body: DeckCreate | DeckUpdate) -> dict[str, Any]:
    data = body.model_dump(exclude_unset=True)
    return {
        "languageId": data.get("languageId", ""),
        "name": data.get("name", ""),
        "description": data.get("description"),
        "courseId": data.get("courseId"),
        "status": data.get("status", "draft"),
        "version": data.get("version", "1.0"),
        "image": data.get("image"),
        "defaultEase": data.get("defaultEase"),
        "locale": data.get("locale"),
        "companionToStoryId": data.get("companionToStoryId"),
    }


def _to_response(manifest: dict[str, Any], cards: list[dict[str, Any]]) -> DeckResponse:
    return DeckResponse(
        id=manifest["id"],
        languageId=manifest.get("languageId", ""),
        name=manifest.get("name", ""),
        description=manifest.get("description"),
        courseId=manifest.get("courseId"),
        authorId=manifest.get("authorId"),
        status=manifest.get("status", "draft"),
        version=manifest.get("version", "1.0"),
        cardCount=manifest.get("cardCount", 0),
        image=manifest.get("image"),
        defaultEase=manifest.get("defaultEase"),
        locale=manifest.get("locale"),
        createdAt=manifest.get("createdAt"),
        updatedAt=manifest.get("updatedAt"),
        companionToStoryId=manifest.get("companionToStoryId"),
        cards=cards,
    )


@router.get("", response_model=list[DeckResponse])
async def list_my_decks(
    repo: DeckRepo,
    user: CurrentUser,
    language_id: str | None = None,
    deck_status: str | None = None,
    exclude_companion_decks: bool = Query(False, description="Exclude companion decks (for community browse)"),
) -> Any:
    """List decks owned by the current user (for My Content or Link existing)."""
    r = _require_deck_repo(repo)
    manifests = await r.list_manifests(
        language_id=language_id,
        author_id=user.id,
        status=deck_status,
        exclude_companion=exclude_companion_decks,
    )
    result = []
    for m in manifests:
        deck = await r.get_deck(m["id"])
        if deck:
            result.append(_to_response(deck, deck.get("cards", [])))
    return result


@router.post("", response_model=DeckResponse, status_code=status.HTTP_201_CREATED)
async def create_deck(
    body: DeckCreate,
    repo: DeckRepo,
    user: CurrentUser,
) -> Any:
    """Create a new community deck (draft by default) or companion deck."""
    r = _require_deck_repo(repo)
    deck_id = f"comm-{uuid.uuid4().hex[:12]}"
    manifest = _manifest_from_body(body)
    manifest["authorId"] = user.id
    manifest["id"] = deck_id
    manifest["status"] = body.status if hasattr(body, "status") else "draft"
    await r.upsert_deck(deck_id, manifest, body.cards)
    deck = await r.get_deck(deck_id)
    if not deck:
        raise HTTPException(status_code=500, detail="Deck creation failed")
    return _to_response(deck, deck.get("cards", []))


@router.get("/batch", response_model=list[DeckResponse])
async def get_decks_batch(
    repo: DeckRepo,
    user: CurrentUser,
    ids: str = Query(..., description="Comma-separated deck IDs"),
) -> Any:
    """Fetch multiple decks by ID. Returns only decks the user can access (published or owned)."""
    if not ids.strip():
        return []
    deck_ids = [s.strip() for s in ids.split(",") if s.strip()]
    if not deck_ids:
        return []
    r = _require_deck_repo(repo)
    decks = await r.get_decks_batch(deck_ids)
    result = []
    for deck in decks:
        author = deck.get("authorId")
        deck_status = deck.get("status", "published")
        if author and author != user.id and deck_status == "draft":
            continue
        result.append(_to_response(deck, deck.get("cards", [])))
    return result


@router.get("/admin", response_model=list[DeckResponse])
async def list_admin_decks(
    repo: DeckRepo,
    user: CurrentUser,
    status: str | None = Query(None, description="Filter by status: draft, published"),
    language_id: str | None = Query(None, description="Filter by language"),
) -> Any:
    """List all decks for admin approval. Excludes companion decks (tied to stories) and personal vocab decks."""
    r = _require_deck_repo(repo)
    manifests = await r.list_manifests(
        language_id=language_id,
        author_id=None,
        status=status,
        exclude_companion=True,
    )
    result = []
    for m in manifests:
        deck_id = m.get("id", "")
        if deck_id.startswith("vocab-"):
            continue
        deck = await r.get_deck(deck_id)
        if deck:
            result.append(_to_response(deck, deck.get("cards", [])))
    return result


@router.patch("/admin/{deck_id}/status", response_model=DeckResponse)
async def admin_update_deck_status(
    deck_id: str,
    repo: DeckRepo,
    user: CurrentUser,
    status: str = Query(..., description="draft | published"),
) -> Any:
    """Approve (published) or reject (draft) a deck. No RBAC for now — all users have access."""
    if status not in ("draft", "published"):
        raise HTTPException(status_code=400, detail="status must be draft or published")
    r = _require_deck_repo(repo)
    existing = await r.get_deck(deck_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Deck not found")
    manifest = {**existing, "status": status, "authorId": existing.get("authorId")}
    await r.upsert_deck(deck_id, manifest, existing.get("cards", []))
    deck = await r.get_deck(deck_id)
    if not deck:
        raise HTTPException(status_code=500, detail="Deck update failed")
    return _to_response(deck, deck.get("cards", []))


def _dedupe_cards(existing: list[dict], new_cards: list[dict]) -> list[dict]:
    """Merge new cards into existing, deduping by front+back. Assigns new ids to added cards."""
    existing_ids = {c.get("id") for c in existing if c.get("id")}
    seen: set[tuple[str, str]] = {
        (str(c.get("front", "")).strip(), str(c.get("back", "")).strip())
        for c in existing
    }
    merged = list(existing)
    for c in new_cards:
        key = (str(c.get("front", "")).strip(), str(c.get("back", "")).strip())
        if key not in seen:
            seen.add(key)
            card = dict(c)
            if card.get("id") in existing_ids or not card.get("id"):
                card["id"] = f"card-{uuid.uuid4().hex[:12]}"
            existing_ids.add(card["id"])
            merged.append(card)
    return merged


@router.get("/my-vocab", response_model=DeckResponse)
async def get_my_vocab_deck(
    repo: DeckRepo,
    sub_repo: SubRepo,
    user: RegisteredUser,
    language_id: str = Query(..., description="Language ID for the vocab deck"),
) -> Any:
    """Get or create the user's 'My Vocab' deck for a language. Used for add-to-vocab from stories.
    Auto-subscribes the user so the deck appears in SRS and deck manager."""
    r = _require_deck_repo(repo)
    manifests = await r.list_manifests(
        language_id=language_id,
        author_id=user.id,
        status=None,
        exclude_companion=True,
    )
    lang_names = {"ko": "Korean", "ja": "Japanese", "zh": "Chinese", "es": "Spanish"}
    vocab_name = f"My Vocab ({lang_names.get(language_id, language_id)})"
    for m in manifests:
        if "my vocab" in (m.get("name") or "").lower():
            deck = await r.get_deck(m["id"])
            if deck:
                deck_id = deck.get("id")
                if deck_id and sub_repo:
                    await sub_repo.add(user.id, "deck", deck_id)
                return _to_response(deck, deck.get("cards", []))
    deck_id = f"vocab-{user.id[:8]}-{language_id}-{uuid.uuid4().hex[:6]}"
    manifest = {
        "id": deck_id,
        "languageId": language_id,
        "name": vocab_name,
        "description": "Words and phrases saved from reading.",
        "authorId": user.id,
        "status": "published",
    }
    await r.upsert_deck(deck_id, manifest, [])
    if sub_repo:
        await sub_repo.add(user.id, "deck", deck_id)
    deck = await r.get_deck(deck_id)
    if not deck:
        raise HTTPException(status_code=500, detail="Vocab deck creation failed")
    return _to_response(deck, deck.get("cards", []))


@router.post("/{deck_id}/cards", response_model=DeckResponse)
async def add_cards_to_deck(
    deck_id: str,
    body: AddCardsRequest,
    repo: DeckRepo,
    user: CurrentUser,
) -> Any:
    """Append cards to a deck. User must own the deck. Dedupes by front+back."""
    r = _require_deck_repo(repo)
    existing = await r.get_deck(deck_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Deck not found")
    if existing.get("authorId") != user.id:
        raise HTTPException(status_code=403, detail="Only the deck author can add cards")
    current_cards = existing.get("cards", [])
    merged = _dedupe_cards(current_cards, body.cards)
    manifest = dict(existing)
    manifest["id"] = deck_id
    manifest["authorId"] = existing.get("authorId") or user.id
    await r.upsert_deck(deck_id, manifest, merged)
    deck = await r.get_deck(deck_id)
    if not deck:
        raise HTTPException(status_code=500, detail="Deck update failed")
    return _to_response(deck, deck.get("cards", []))


@router.get("/{deck_id}", response_model=DeckResponse)
async def get_deck(
    deck_id: str,
    repo: DeckRepo,
    user: CurrentUser,
) -> Any:
    """Get a deck by id. User must own it (for drafts) or it must be published."""
    r = _require_deck_repo(repo)
    deck = await r.get_deck(deck_id)
    if not deck:
        raise HTTPException(status_code=404, detail="Deck not found")
    author = deck.get("authorId")
    deck_status = deck.get("status", "published")
    if author and author != user.id and deck_status == "draft":
        raise HTTPException(status_code=404, detail="Deck not found")
    return _to_response(deck, deck.get("cards", []))


@router.put("/{deck_id}", response_model=DeckResponse)
async def update_deck(
    deck_id: str,
    body: DeckUpdate,
    repo: DeckRepo,
    user: CurrentUser,
) -> Any:
    """Update a deck. User must be the author."""
    r = _require_deck_repo(repo)
    existing = await r.get_deck(deck_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Deck not found")
    author = existing.get("authorId")
    if author and author != user.id:
        raise HTTPException(status_code=403, detail="Only the author can update this deck")
    patch = body.model_dump(exclude_unset=True)
    manifest = dict(existing)
    manifest["languageId"] = patch.get("languageId", manifest.get("languageId", ""))
    manifest["name"] = patch.get("name", manifest.get("name", ""))
    manifest["description"] = patch.get("description", manifest.get("description"))
    if "image" in patch:
        manifest["image"] = patch["image"] or None
    if "defaultEase" in patch:
        manifest["defaultEase"] = patch["defaultEase"]
    manifest["status"] = patch.get("status", manifest.get("status", "draft"))
    if "companionToStoryId" in patch:
        manifest["companionToStoryId"] = patch["companionToStoryId"]
    manifest["authorId"] = author or user.id
    manifest["id"] = deck_id
    cards = patch["cards"] if "cards" in patch else existing.get("cards", [])
    await r.upsert_deck(deck_id, manifest, cards)
    deck = await r.get_deck(deck_id)
    if not deck:
        raise HTTPException(status_code=500, detail="Deck update failed")
    return _to_response(deck, deck.get("cards", []))


@router.patch("/{deck_id}/status", response_model=DeckResponse)
async def update_deck_status(
    deck_id: str,
    repo: DeckRepo,
    user: CurrentUser,
    status: str = Query(..., description="draft | published"),
) -> Any:
    """Change deck status (draft | published). User must be the author."""
    if status not in ("draft", "published"):
        raise HTTPException(status_code=400, detail="status must be draft or published")
    r = _require_deck_repo(repo)
    existing = await r.get_deck(deck_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Deck not found")
    author = existing.get("authorId")
    if author and author != user.id:
        raise HTTPException(status_code=403, detail="Only the author can update this deck")
    manifest = {**existing, "status": status, "authorId": author or user.id}
    await r.upsert_deck(deck_id, manifest, existing.get("cards", []))
    deck = await r.get_deck(deck_id)
    if not deck:
        raise HTTPException(status_code=500, detail="Deck update failed")
    return _to_response(deck, deck.get("cards", []))
