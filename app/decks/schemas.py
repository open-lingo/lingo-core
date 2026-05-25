"""Pydantic schemas for deck API."""

from pydantic import BaseModel, Field


class DeckCreate(BaseModel):
    """Create or save draft deck."""

    languageId: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    image: str | None = Field(default=None, description="Cover/thumbnail URL")
    defaultEase: float | None = Field(default=None, ge=1.3, le=3.0, description="Initial ease for new cards (SM-2)")
    status: str = Field(default="draft", description="draft | published")
    cards: list[dict] = Field(default_factory=list)
    """If set, deck is a companion deck (tied to a story). Excluded from community browse."""
    companionToStoryId: str | None = Field(default=None, description="Story ID or 'pending' for draft companion")


class DeckUpdate(BaseModel):
    """Update deck. Omitted fields keep existing values."""

    languageId: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    image: str | None = None
    defaultEase: float | None = Field(default=None, ge=1.3, le=3.0, description="Initial ease for new cards (SM-2)")
    status: str | None = Field(default=None, description="draft | published")
    cards: list[dict] | None = None  # None = keep existing cards
    companionToStoryId: str | None = None


class AddCardsRequest(BaseModel):
    """Append cards to a deck. Deduped by front+back."""

    cards: list[dict] = Field(..., min_length=1, description="Cards to add")


class DeckResponse(BaseModel):
    """Deck with manifest + cards."""

    id: str
    languageId: str
    name: str
    description: str | None = None
    courseId: str | None = None
    authorId: str | None = None
    status: str = "draft"
    version: str = "1.0"
    cardCount: int = 0
    image: str | None = None
    defaultEase: float | None = Field(default=None, ge=1.3, le=3.0, description="Initial ease for new cards")
    locale: str | None = None
    createdAt: str | None = None
    updatedAt: str | None = None
    companionToStoryId: str | None = None
    cards: list[dict] = Field(default_factory=list)
    voteCount: int = 0


class DeckVoteState(BaseModel):
    """Vote state for a single deck from the current user's perspective."""

    count: int = 0
    voted: bool = False
