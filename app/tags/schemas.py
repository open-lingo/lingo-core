"""Pydantic schemas for the tag API."""

from pydantic import BaseModel, Field

# Canonical slug shape — lowercase, starts with a letter, may contain digits
# and dashes. Length 2-41. The router uses this to validate POST /admin/tags.
SLUG_PATTERN = r"^[a-z][a-z0-9-]{1,40}$"


class TagResponse(BaseModel):
    """A canonical, admin-curated tag."""

    slug: str
    display_name: str
    description: str | None = None
    color: str | None = None
    created_at: str | None = None


class TagCreate(BaseModel):
    """Create a new canonical tag (admin only)."""

    slug: str = Field(..., pattern=SLUG_PATTERN, description="lowercase kebab-case slug")
    display_name: str = Field(..., min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=400)
    color: str | None = Field(default=None, max_length=32)


class TagUpdate(BaseModel):
    """Patch a canonical tag (admin only). Omitted fields keep prior value."""

    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=400)
    color: str | None = Field(default=None, max_length=32)
