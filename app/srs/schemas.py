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
    lastReviewedAt: str | None = Field(
        default=None,
        description=(
            "Top-level ISO timestamp of the most-recent review across "
            "modalities. The FE writes this so the server LWW merge can "
            "distinguish two same-day reviews — comparing the modality "
            "``lastReviewDate`` alone (date-only) made same-day re-reviews "
            "look equal and silently rejected the client's newer state."
        ),
    )
    known: bool = Field(
        default=False,
        description=(
            "Set by the FE's test-out/placement seed writer "
            "(`testOutSeed.ts`) when the seeded interval is >= "
            "KNOWN_THRESHOLD_DAYS (90 days) — the learner tested out far "
            "enough past this atom's module that it's suppressed from the "
            "flashcard reviewer + review-lesson intake. Added lane SRSGAPS "
            "2026-09-18 (GAP A): this schema previously had no field for it "
            "at all, so pydantic's default extra='ignore' silently dropped "
            "it on every /srs/sync push — a device that had never seen the "
            "card locally (a fresh pull via /srs/state) always got `known` "
            "missing, i.e. NOT suppressed, which is the root cause of the "
            "phone-vs-second-device due-count mismatch on record. Additive: "
            "both storage backends persist the whole state blob opaquely "
            "(no column-level change needed in sqlite or Dynamo), so this "
            "field alone is the fix."
        ),
    )


#: Maximum cards accepted in a single sync (or delete) request.
#:
#: The binding constraint is NOT the Lambda Function URL's 6 MB payload cap —
#: at a measured ~437 bytes/card that would allow ~14k. It is the function's
#: 30s timeout against ``upsert_cards``, which issues one conditional
#: UpdateItem per card. Sized so a batch completes well inside the timeout even
#: if the writes were fully serialized at ~8ms each (~8s for 1000, ~2x margin),
#: so this stays safe if the concurrency in ``db/dynamo/srs.py`` ever regresses.
#:
#: An oversized push now fails fast with a 422 instead of burning the full 30s
#: and timing out — which used to wedge the client: a timeout returns no card
#: ids, nothing gets marked synced, and the identical oversized payload is
#: retried forever. Clients chunk to this limit (see ``SRS_SYNC_CHUNK_SIZE`` in
#: the frontend's ``engine/srsSync.ts``); the cap is the backstop, not the
#: mechanism.
MAX_SYNC_CARDS = 1000


class SRSSyncRequest(BaseModel):
    """Client pushes dirty cards. Keys are card IDs."""

    cards: dict[str, SRSCardState] = Field(max_length=MAX_SYNC_CARDS)
    syncedAt: str | None = None


class SRSSyncResponse(BaseModel):
    """Server returns the merged state for synced cards.

    ``cards`` holds only the ids that actually landed — a card that failed
    its individual write is OMITTED, not included with stale/default data,
    so a client checking "is this id in the response" (the existing
    contract — see `lingo/src/features/flashcards/engine/srsSync.ts`
    `performSyncNow`) already treats it as unsynced with no client change
    required. ``failedCardIds`` is additive: it names the same omitted ids
    explicitly, for callers that want to surface a failure instead of
    inferring it from an absence.
    """

    cards: dict[str, SRSCardState]
    syncedAt: str
    failedCardIds: list[str] = Field(default_factory=list)


class SRSStateResponse(BaseModel):
    """Full SRS map for a user."""

    cards: dict[str, SRSCardState]


class SRSDeleteRequest(BaseModel):
    """Delete SRS state for specific cards."""

    # Same per-card-round-trip shape as the sync path (``delete_cards`` loops
    # one DeleteItem per id), so it carries the same bound.
    cardIds: list[str] = Field(max_length=MAX_SYNC_CARDS)


class SRSDeleteResponse(BaseModel):
    deleted: int


class SRSClearResponse(BaseModel):
    deleted: int
