from pydantic import BaseModel, Field

# -- User record --


class UserCreate(BaseModel):
    """Payload for registering a new user (POST /me)."""

    username: str = Field(min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_-]+$")
    display_name: str = Field(min_length=1, max_length=100)


class UserUpdate(BaseModel):
    """Partial update for the user record (PATCH /me)."""

    username: str | None = Field(
        default=None, min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_-]+$"
    )
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    profile_picture_key: str | None = None
    status: str | None = None


class UserResponse(BaseModel):
    """Public-facing user representation."""

    id: str
    auth0_id: str
    username: str
    display_name: str
    profile_picture_key: str | None = None
    status: str = "active"
    created_at: str
    updated_at: str


# -- User settings (preferences blob) --


class UserSettings(BaseModel):
    """User preferences — intentionally flexible to match what the frontend stores.

    Known keys (from the frontend ``settings/types.ts``):
      - theme: "light" | "dark" | "system"
      - learningLanguage: language id string
      - uiLocale: locale code
    Extra keys are preserved so the frontend can evolve without backend changes.
    """

    model_config = {"extra": "allow"}

    theme: str | None = None
    learningLanguage: str | None = None
    uiLocale: str | None = None


class UserSettingsPatch(BaseModel):
    """Partial update — any subset of UserSettings fields."""

    model_config = {"extra": "allow"}

    theme: str | None = None
    learningLanguage: str | None = None
    uiLocale: str | None = None
