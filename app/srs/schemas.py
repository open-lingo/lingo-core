from datetime import datetime
from typing import Any

from pydantic import BaseModel, RootModel, model_validator


# Why: Frontend ships FSRS-6 modal state ({recognition, production, ...}); the
# old SM-2 schema 422'd every sync. We store the payload opaquely and only
# require ``lastReviewedAt`` for last-write-wins merge.
class SRSCardState(RootModel[dict[str, Any]]):
    """Opaque per-card SRS blob.

    Only one server-merge key is required: ``lastReviewedAt`` (ISO timestamp).
    Everything else round-trips byte-equivalent so the engine can evolve
    without touching the API surface.
    """

    root: dict[str, Any]

    @model_validator(mode="after")
    def _require_last_reviewed_at(self) -> "SRSCardState":
        last = self.root.get("lastReviewedAt")
        if not isinstance(last, str):
            raise ValueError(
                "Each card requires a string 'lastReviewedAt' ISO timestamp"
            )
        try:
            datetime.fromisoformat(last)
        except ValueError as e:
            raise ValueError(f"lastReviewedAt must be ISO-8601: {e}") from e
        return self


class SRSSyncRequest(BaseModel):
    """Client pushes dirty cards. Keys are card IDs."""

    cards: dict[str, SRSCardState]


class SRSSyncResponse(BaseModel):
    """Server returns the merged state for synced cards."""

    cards: dict[str, dict[str, Any]]
    syncedAt: str


class SRSStateResponse(BaseModel):
    """Full SRS map for a user."""

    cards: dict[str, dict[str, Any]]


class SRSDeleteRequest(BaseModel):
    """Delete SRS state for specific cards."""

    cardIds: list[str]


class SRSDeleteResponse(BaseModel):
    deleted: int


class SRSClearResponse(BaseModel):
    deleted: int


__all__ = [
    "SRSCardState",
    "SRSSyncRequest",
    "SRSSyncResponse",
    "SRSStateResponse",
    "SRSDeleteRequest",
    "SRSDeleteResponse",
    "SRSClearResponse",
]
