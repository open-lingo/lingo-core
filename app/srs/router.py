from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.dependencies import get_registered_user
from app.auth.schemas import TokenPayload
from app.db.protocols import SRSRepository
from app.db.provider import get_srs_repo
from app.srs.schemas import (
    SRSClearResponse,
    SRSDeleteRequest,
    SRSDeleteResponse,
    SRSStateResponse,
    SRSSyncRequest,
    SRSSyncResponse,
)

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
    """
    if not body.cards:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No cards to sync")

    # Why: SRSCardState is a RootModel; ``state.root`` is the opaque payload.
    cards_dict = {cid: state.root for cid, state in body.cards.items()}
    merged = await repo.upsert_cards(user.id, cards_dict)
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
