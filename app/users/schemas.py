from typing import Literal

from pydantic import BaseModel, Field

from app.auth.roles import Role

# -- User record --

AccountStatus = Literal["active", "banned"]
CommunityStatus = Literal["active", "banned"]


class UserCreate(BaseModel):
    """Payload for registering a new user (POST /me)."""

    username: str = Field(min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_-]+$")
    display_name: str = Field(min_length=1, max_length=100)


class UserUpdate(BaseModel):
    """Partial update for the user record (admin PATCH). Includes ban fields."""

    username: str | None = Field(
        default=None, min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_-]+$"
    )
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    profile_picture_key: str | None = None
    status: AccountStatus | None = None
    status_expiration: str | None = Field(default=None, description="ISO datetime when ban expires")
    community_status: CommunityStatus | None = None
    community_status_expiration: str | None = Field(
        default=None, description="ISO datetime when community ban expires"
    )
    bio: str | None = Field(default=None, max_length=500, description="Profile bio/status text")
    role: Role | None = Field(default=None, description="user | trusted_creator | moderator | admin | super_admin")


class MeUpdate(BaseModel):
    """Partial update for own profile (PATCH /me). No ban-related fields."""

    username: str | None = Field(
        default=None, min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_-]+$"
    )
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    profile_picture_key: str | None = None
    bio: str | None = Field(default=None, max_length=500, description="Profile bio/status text")


class UserResponse(BaseModel):
    """Public-facing user representation."""

    id: str
    auth0_id: str
    username: str
    display_name: str
    profile_picture_key: str | None = None
    bio: str | None = None
    status: str = "active"
    status_expiration: str | None = None
    community_status: str | None = None
    community_status_expiration: str | None = None
    role: str = "user"
    created_at: str
    updated_at: str


# -- User settings (preferences blob) --


class UserSettings(BaseModel):
    """User preferences — intentionally flexible to match what the frontend stores.

    Flat keys (legacy):
      - theme: theme id (e.g. "light" | "dark" | "sepia" | "amoled")
      - learningLanguage: language id string
      - uiLocale: locale code
    Nested keys (from frontend shared/settings/types):
      - appearance: { themeId, darkMode }
      - accessibility: { reducedMotion, highContrast?, fontScale? }
      - audio: { soundEnabled }
      - notifications: { dailyReminderTime?, reminderEnabled }
      - learning: { learningLanguageId, uiLocale, onboardingCompleted }
      - display: { dateLocale?, timezoneOverride? }
    Extra keys are preserved so the frontend can evolve without backend changes.
    """

    model_config = {"extra": "allow"}

    theme: str | None = None
    learningLanguage: str | None = None
    uiLocale: str | None = None
    appearance: dict | None = None
    accessibility: dict | None = None
    audio: dict | None = None
    notifications: dict | None = None
    learning: dict | None = None
    display: dict | None = None


class UserSettingsPatch(BaseModel):
    """Partial update — any subset of UserSettings fields. Nested objects are merged."""

    model_config = {"extra": "allow"}

    theme: str | None = None
    learningLanguage: str | None = None
    uiLocale: str | None = None
    appearance: dict | None = None
    accessibility: dict | None = None
    audio: dict | None = None
    notifications: dict | None = None
    learning: dict | None = None
    display: dict | None = None


# -- Subscriptions (content user has added) --


class SubscriptionItem(BaseModel):
    """A single subscription."""

    contentType: str
    contentId: str
    createdAt: str | None = None
    enabled: bool = True
    newCardsPerDay: int = 5
    newCardOrder: str = Field(default="ordered", description="ordered | shuffled")


class SubscriptionCreate(BaseModel):
    """Add a subscription."""

    contentType: str = Field(description="deck, addon, story")
    contentId: str = Field(min_length=1)


class SubscriptionSettingsPatch(BaseModel):
    """Update subscription settings (enabled, new card limits, etc)."""

    enabled: bool | None = None
    newCardsPerDay: int | None = Field(default=None, ge=0, le=100)
    newCardOrder: str | None = Field(default=None, description="ordered | shuffled")
