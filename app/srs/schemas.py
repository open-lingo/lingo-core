from pydantic import BaseModel, Field


class SRSCardState(BaseModel):
    """SM-2 state for a single flashcard."""

    easeFactor: float = Field(default=2.5, ge=1.3)
    interval: int = Field(default=0, ge=0)
    dueDate: str
    repetitions: int = Field(default=0, ge=0)
    lastReviewDate: str
    lastSyncedAt: str | None = None
    buriedUntil: str | None = Field(default=None, description="YYYY-MM-DD; if set and > today, card excluded from queue")


class SRSSyncRequest(BaseModel):
    """Client pushes dirty cards. Keys are card IDs."""

    cards: dict[str, SRSCardState]


class SRSSyncResponse(BaseModel):
    """Server returns the merged state for synced cards."""

    cards: dict[str, SRSCardState]
    syncedAt: str


class SRSStateResponse(BaseModel):
    """Full SRS map for a user."""

    cards: dict[str, SRSCardState]


class SRSDeleteRequest(BaseModel):
    """Delete SRS state for specific cards."""

    cardIds: list[str]


class SRSDeleteResponse(BaseModel):
    deleted: int


class SRSClearResponse(BaseModel):
    deleted: int
