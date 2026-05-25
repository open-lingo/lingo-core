from typing import Literal

from pydantic import BaseModel, Field


SRSPhase = Literal["new", "learning", "review", "relearning"]


class SRSModalityState(BaseModel):
    """FSRS-6 state for one direction (recognition or production)."""

    stability: float = Field(default=0, ge=0)
    difficulty: float = Field(default=0, ge=0)
    state: SRSPhase = "new"
    interval: int = Field(default=0, ge=0)
    dueDate: str
    lastReviewDate: str
    reps: int = Field(default=0, ge=0)
    lapses: int = Field(default=0, ge=0)
    learningSteps: int | None = None


class SRSCardState(BaseModel):
    """FSRS-6 state with recognition/production modality split."""

    recognition: SRSModalityState
    production: SRSModalityState
    lastSyncedAt: str | None = None
    buriedUntil: str | None = Field(
        default=None,
        description="YYYY-MM-DD; if set and > today, card excluded from queue",
    )


class SRSSyncRequest(BaseModel):
    """Client pushes dirty cards. Keys are card IDs."""

    cards: dict[str, SRSCardState]
    syncedAt: str | None = None


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
