"""Pydantic v2 schemas for the social API.

Friends, blocks, leaderboards, profile, activity feed (stub).
"""

from typing import Literal

from pydantic import BaseModel, Field

# ── Friends ────────────────────────────────────────────────────────────────


class FriendItem(BaseModel):
    user_id: str
    username: str
    display_name: str
    profile_picture_key: str | None = None
    xp: int = 0
    streak: int = 0
    lastActiveAt: str | None = None
    friendedAt: str


class FriendRequestItem(BaseModel):
    user_id: str
    username: str
    display_name: str
    requestedAt: str


class FriendRequestsResponse(BaseModel):
    incoming: list[FriendRequestItem]
    outgoing: list[FriendRequestItem]


class FriendRequestCreate(BaseModel):
    """Either ``toUsername`` or ``toUserId`` is required (username preferred)."""

    toUsername: str | None = Field(default=None, min_length=3, max_length=30)
    toUserId: str | None = None


class FriendRequestStatus(BaseModel):
    status: Literal["pending", "accepted", "exists"]


# ── Blocks ─────────────────────────────────────────────────────────────────


class BlockedUserItem(BaseModel):
    user_id: str
    username: str
    display_name: str
    blockedAt: str


# ── Leaderboards ───────────────────────────────────────────────────────────


class LeaderboardEntry(BaseModel):
    user_id: str
    username: str
    display_name: str
    profile_picture_key: str | None = None
    xp_this_period: int
    rank: int


class LeaderboardResponse(BaseModel):
    bucket: str
    entries: list[LeaderboardEntry]
    total: int
    my_rank: int | None = None


class MyLeaderboardSlot(BaseModel):
    bucket: str
    xp: int
    rank: int | None
    total: int


class MyLeaderboardSummary(BaseModel):
    weekly: MyLeaderboardSlot | None
    monthly: MyLeaderboardSlot | None
    lang: str | None


# ── Public profile ─────────────────────────────────────────────────────────


FriendshipStatus = Literal[
    "none", "friend", "request_in", "request_out", "blocked", "self"
]


class PublicProfileResponse(BaseModel):
    user_id: str
    username: str
    display_name: str
    profile_picture_key: str | None = None
    bio: str | None = None
    learning_language: str | None = None
    joined_at: str
    streak: int = 0
    xp: int = 0
    friendship_status: FriendshipStatus | None = None


# ── Activity feed (stub) ───────────────────────────────────────────────────


class ActivityFeedResponse(BaseModel):
    items: list[dict]
    cursor: str | None = None
