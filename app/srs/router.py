import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.dependencies import get_registered_user
from app.auth.schemas import TokenPayload
from app.db.protocols import SRSRepository
from app.db.provider import get_srs_repo
from app.events.publisher import publish as publish_event
from app.srs.schemas import (
    SRSClearResponse,
    SRSDeleteRequest,
    SRSDeleteResponse,
    SRSStateResponse,
    SRSSyncRequest,
    SRSSyncResponse,
)

logger = logging.getLogger("lingo.srs")

router = APIRouter(tags=["srs"])

CurrentUser = Annotated[TokenPayload, Depends(get_registered_user)]
SRSRepo = Annotated[SRSRepository, Depends(get_srs_repo)]


@router.get("/state", response_model=SRSStateResponse)
async def get_state(user: CurrentUser, repo: SRSRepo) -> Any:
    cards = await repo.get_all(user.id)
    return {"cards": cards}


@router.get("/due", response_model=SRSStateResponse)
async def get_due_cards(
    user: CurrentUser,
    repo: SRSRepo,
    on_or_before: str = Query(..., description="YYYY-MM-DD"),
) -> Any:
    cards = await repo.get_due_cards(user.id, on_or_before)
    return {"cards": cards}


@router.post("/sync", response_model=SRSSyncResponse)
async def sync_cards(body: SRSSyncRequest, user: CurrentUser, repo: SRSRepo) -> Any:
    """Sync dirty cards from the client.

    Uses last-write-wins by ``lastReviewedAt``: if the server has a newer
    review for a card, the server version is kept.

    Fires one ``review_completed`` event per card whose ``lastReviewDate``
    is today — this is the signal lingo-async needs to advance the
    daily-flashcards quest (unit="cards"). Cards with older dates are
    cross-device sync residue (state moved over without a fresh review)
    and don't fire events.
    """
    if not body.cards:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No cards to sync")

    cards_dict = {cid: state.model_dump(mode="json") for cid, state in body.cards.items()}
    merged = await repo.upsert_cards(user.id, cards_dict)

    # Tally today's reviews and fire ONE batched ``review_completed`` event
    # carrying the count. We count each modality whose ``lastReviewDate``
    # is today (UTC, matching the FE's ``new Date().toISOString().slice(0,10)``).
    # The quest evaluator uses ``count`` as the delta for unit="cards"
    # quests, so 6 reviews in one sync = +6 progress in one event instead
    # of six separate events spamming the inspector + dispatch loop.
    # Best-effort: a publish failure must not break the sync response.
    today_iso = datetime.now(UTC).date().isoformat()
    review_count = 0
    last_card_id = ""
    last_modality: str = "recognition"
    for card_id, state in body.cards.items():
        if state.recognition.lastReviewDate == today_iso:
            review_count += 1
            last_card_id, last_modality = card_id, "recognition"
        if state.production.lastReviewDate == today_iso:
            review_count += 1
            last_card_id, last_modality = card_id, "production"
    if review_count > 0:
        try:
            publish_event(
                {
                    "type": "review_completed",
                    "version": 1,
                    "user_id": user.id,
                    "card_id": last_card_id,
                    "modality": last_modality,
                    "rating": "good",
                    "count": review_count,
                }
            )
        except Exception:
            logger.exception(
                "review_completed batch publish failed count=%d", review_count
            )

    return {
        "cards": merged,
        "syncedAt": datetime.now(UTC).isoformat(),
    }


@router.delete("/cards", response_model=SRSDeleteResponse)
async def delete_cards(body: SRSDeleteRequest, user: CurrentUser, repo: SRSRepo) -> Any:
    if not body.cardIds:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No card IDs provided")
    count = await repo.delete_cards(user.id, body.cardIds)
    return {"deleted": count}


@router.delete("/all", response_model=SRSClearResponse)
async def clear_all(user: CurrentUser, repo: SRSRepo) -> Any:
    count = await repo.clear_all(user.id)
    return {"deleted": count}
