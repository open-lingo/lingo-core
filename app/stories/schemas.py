"""Pydantic schemas for story API."""

from pydantic import BaseModel, Field


class StoryCreate(BaseModel):
    """Create a new story."""

    languageId: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)
    companionDeckId: str = Field(..., min_length=1, description="Vocab deck for this story")
    body: str = Field(default="", description="Story body with [card:id]display[/card] embeds")


class StoryUpdate(BaseModel):
    """Update story. Omitted fields keep existing values."""

    languageId: str | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    companionDeckId: str | None = None
    body: str | None = None


class StoryResponse(BaseModel):
    """Full story response."""

    id: str
    languageId: str
    title: str
    description: str | None = None
    companionDeckId: str
    body: str = ""
    authorId: str | None = None
    status: str = "draft"
    createdAt: str | None = None
    updatedAt: str | None = None
