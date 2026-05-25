"""Quest API request/response schemas.

Mirrors ``lingo/src/features/quests/types.ts``. The frontend renders
``title``/``description`` as i18n keys; the API speaks keys too.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

QuestType = Literal["daily", "weekly", "random", "friend"]
QuestStatus = Literal["active", "claimable", "completed", "expired"]


class QuestRewards(BaseModel):
    """Reward bundle granted on claim."""

    lingots: int | None = None
    xp: int | None = None
    ad_free_minutes: int | None = Field(default=None, alias="adFreeMinutes")
    streak_shield: bool | None = Field(default=None, alias="streakShield")

    model_config = ConfigDict(populate_by_name=True)


class QuestProgress(BaseModel):
    current: int
    target: int
    unit: str


class Quest(BaseModel):
    """Public-facing quest. Camel-case aliases match the TypeScript shape."""

    id: str
    type: QuestType
    title: str  # i18n key
    description: str  # i18n key
    emoji: str = ""
    progress: QuestProgress
    rewards: QuestRewards = Field(default_factory=QuestRewards)
    expires_at: int | None = Field(default=None, alias="expiresAt")
    friend_id: str | None = Field(default=None, alias="friendId")
    friend_display_name: str | None = Field(default=None, alias="friendDisplayName")
    status: QuestStatus

    model_config = ConfigDict(populate_by_name=True)


class QuestProgressBody(BaseModel):
    delta: int


class QuestListResponse(BaseModel):
    items: list[Quest] = Field(default_factory=list)


class QuestClaimResponse(BaseModel):
    quest: Quest
    lingots_granted: int = Field(0, alias="lingotsGranted")
    xp_granted: int = Field(0, alias="xpGranted")
    reward_granted: bool = Field(False, alias="rewardGranted")

    model_config = ConfigDict(populate_by_name=True)


class QuestRefreshResponse(BaseModel):
    removed: int
    seeded: int
