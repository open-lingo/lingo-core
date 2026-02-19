from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.dependencies import get_current_user
from app.auth.schemas import TokenPayload
from app.db.dependencies import get_srs_repo
from app.db.protocols import SRSRepository
from app.srs.schemas import (
    SRSCardState,
    SRSClearResponse,
    SRSDeleteRequest,
    SRSDeleteResponse,
    SRSStateResponse,
    SRSSyncRequest,
    SRSSyncResponse,
)

router = APIRouter(prefix="/api/core/srs/v1", tags=["srs"])

CurrentUser = Annotated[TokenPayload, Depends(get_current_user)]
SRSRepo = Annotated[SRSRepository, Depends(get_srs_repo)]


@router.get("/state", response_model=SRSStateResponse)
async def get_state(user: CurrentUser, repo: SRSRepo) -> Any:
    """Return the full SRS map for the current user."""
    cards = await repo.get_all(user.sub)
    return {"cards": cards}


@router.post("/sync", response_model=SRSSyncResponse)
async def sync_cards(body: SRSSyncRequest, user: CurrentUser, repo: SRSRepo) -> Any:
    """Sync dirty cards from the client.

    Uses last-write-wins by lastReviewDate: if the server has a newer
    review for a card, the server version is kept.

    Returns the merged state for all affected cards so the client
    can update its local store.
    """
    if not body.cards:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No cards to sync")

    cards_dict = {cid: state.model_dump() for cid, state in body.cards.items()}
    merged = await repo.upsert_cards(user.sub, cards_dict)
    return {
        "cards": merged,
        "syncedAt": datetime.now(UTC).isoformat(),
    }


@router.delete("/cards", response_model=SRSDeleteResponse)
async def delete_cards(body: SRSDeleteRequest, user: CurrentUser, repo: SRSRepo) -> Any:
    """Remove SRS state for specific cards (e.g. when resetting a deck)."""
    if not body.cardIds:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No card IDs provided")
    count = await repo.delete_cards(user.sub, body.cardIds)
    return {"deleted": count}


@router.delete("/all", response_model=SRSClearResponse)
async def clear_all(user: CurrentUser, repo: SRSRepo) -> Any:
    """Nuclear option: wipe all SRS state for the current user."""
    count = await repo.clear_all(user.sub)
    return {"deleted": count}
