from typing import Any

from pydantic import BaseModel


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


class UserProfile(BaseModel):
    displayName: str | None = None
    avatarUrl: str | None = None
