"""Pydantic schemas for the social API.

Snake-case fields throughout, matching the rest of the lingo-core surface.
Date / time values are ISO-8601 strings on the wire (datetime in Python).
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# ─── Friends ─────────────────────────────────────────────────────────────────


class FriendItem(BaseModel):
    user_id: str
    username: str
    display_name: str
    profile_picture_key: str | None = None
    xp: int = 0
    streak: int = 0
    last_active_at: str | None = None
    friended_at: str


class FriendRequestItem(BaseModel):
    user_id: str
    username: str
    display_name: str
    requested_at: str


class FriendRequestsResponse(BaseModel):
    incoming: list[FriendRequestItem] = Field(default_factory=list)
    outgoing: list[FriendRequestItem] = Field(default_factory=list)


class SendFriendRequestBody(BaseModel):
    to_username: str | None = None
    to_user_id: str | None = None


class FriendRequestStatus(BaseModel):
    status: Literal["pending", "accepted", "exists"]


# ─── Blocks ──────────────────────────────────────────────────────────────────


class BlockedUserItem(BaseModel):
    user_id: str
    username: str
    display_name: str
    blocked_at: str


# ─── Leaderboards ────────────────────────────────────────────────────────────


class LeaderboardEntry(BaseModel):
    user_id: str
    username: str
    display_name: str
    profile_picture_key: str | None = None
    xp_this_period: int = 0
    rank: int


class LeaderboardResponse(BaseModel):
    bucket: str
    entries: list[LeaderboardEntry] = Field(default_factory=list)
    total: int = 0
    my_rank: int | None = None


class MyLeaderboardSlot(BaseModel):
    bucket: str
    xp: int = 0
    rank: int | None = None
    total: int = 0


class MyLeaderboardSummary(BaseModel):
    weekly: MyLeaderboardSlot | None = None
    monthly: MyLeaderboardSlot | None = None
    lang: str | None = None


# ─── League spotlight ────────────────────────────────────────────────────────

# Brackets keyed on weekly XP. Picked to ease testing + match a Duolingo-ish
# vibe:
#   0-99      bronze   (tier 1)
#   100-499   silver   (tier 2)
#   500-1499  gold     (tier 3)
#   1500-4999 diamond  (tier 4)
#   5000+     obsidian (tier 4 capped — display only)
LeagueName = Literal["bronze", "silver", "gold", "diamond", "obsidian"]


class LeagueSpotlightResponse(BaseModel):
    league: LeagueName
    league_tier: int
    my_row: LeaderboardEntry | None = None
    rank: int | None = None
    rank_yesterday: int | None = None
    rank_delta_today: int = 0
    # 7-element arrays (one int per day, index 0 = oldest, index 6 = today).
    # The FE adapter expects list[int]; scalar values would silently become []
    # after the Array.isArray() guard in socialAdapters.ts.
    daily_xp: list[int] = Field(default_factory=list)
    friend_median_daily_xp: list[int] = Field(default_factory=list)
    top_three: list[LeaderboardEntry] = Field(default_factory=list)
    promotion_threshold: int | None = None
    demotion_threshold: int | None = None


# ─── Streak snapshot ─────────────────────────────────────────────────────────


class StreakSnapshotResponse(BaseModel):
    my_streak_days: int = 0
    friend_median_streak_days: int = 0
    best_friend_streak_days: int | None = None
    best_friend_username: str | None = None


# ─── Leaderboard bundle ──────────────────────────────────────────────────────

# Aggregates the four leaderboard reads the social page needs into a single
# response so the FE makes one round-trip instead of four. The bundle uses
# ``asyncio.gather`` internally so the four queries also run in parallel
# within the same request (real Lambda concurrency win — multiple awaits
# inside one invocation).


class LeaderboardBundleResponse(BaseModel):
    weekly: LeaderboardResponse
    monthly: LeaderboardResponse
    friends: LeaderboardResponse
    spotlight: LeagueSpotlightResponse


# ─── Public profile ──────────────────────────────────────────────────────────


FriendshipStatus = Literal["none", "friend", "request_in", "request_out", "blocked", "self"]


class AuthoredDeckSample(BaseModel):
    id: str
    name: str
    language: str | None = None


class LeagueBadge(BaseModel):
    """Lightweight league info for public profile rendering. Mirrors the
    FE ``LeagueInfo`` shape but only the fields a stranger needs to see."""

    name: str
    tier_index: int
    emoji: str


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
    # Enriched profile fields. Appended (Pydantic v2 ignores unknown fields by
    # default so older callers reading this shape remain compatible).
    lingots: int = 0
    level: int = 1
    last_active_date: str | None = None
    authored_deck_count: int = 0
    authored_decks_sample: list[AuthoredDeckSample] = []
    # Optional league badge — None when the user has no XP yet.
    league: LeagueBadge | None = None
    # Equipped cosmetics (read from the owner's settings blob). Empty
    # string in settings is normalized to None so the FE can render the
    # bare profile when the owner hasn't equipped anything.
    equipped_decorator_id: str | None = None
    equipped_title_id: str | None = None
    equipped_banner_id: str | None = None


# ─── Activity feed ───────────────────────────────────────────────────────────


ReactionKind = Literal["wave", "fire", "clap", "target"]
REACTION_KINDS: tuple[ReactionKind, ...] = ("wave", "fire", "clap", "target")


class ActivityReaction(BaseModel):
    kind: ReactionKind
    count: int = 0
    mine: bool = False


ActivityKind = Literal[
    "lesson_completed",
    "streak_milestone",
    "level_up",
    "friend_joined",
    "achievement",
]


class ActivityItem(BaseModel):
    id: str
    user_id: str
    username: str
    display_name: str
    profile_picture_key: str | None = None
    kind: ActivityKind
    payload: dict = Field(default_factory=dict)
    created_at: str
    reactions: list[ActivityReaction] = Field(default_factory=list)


class ActivityFeedResponse(BaseModel):
    items: list[ActivityItem] = Field(default_factory=list)
    cursor: str | None = None


# ─── Invites ─────────────────────────────────────────────────────────────────


# Defaults baked in — the spec calls these out as fixed values for the MVP.
DEFAULT_LINGOT_REWARD_INVITER = 100
DEFAULT_LINGOT_REWARD_INVITEE = 50
DEFAULT_AD_FREE_MINUTES_INVITER = 1440
DEFAULT_AD_FREE_MINUTES_INVITEE = 1440
DEFAULT_MONTHLY_CAP = 10
DEFAULT_INVITE_BASE_URL = "https://lingo.app/invite"


class InviteOfferResponse(BaseModel):
    code: str
    url: str
    lingot_reward_inviter: int = DEFAULT_LINGOT_REWARD_INVITER
    lingot_reward_invitee: int = DEFAULT_LINGOT_REWARD_INVITEE
    ad_free_minutes_inviter: int = DEFAULT_AD_FREE_MINUTES_INVITER
    ad_free_minutes_invitee: int = DEFAULT_AD_FREE_MINUTES_INVITEE
    monthly_cap: int = DEFAULT_MONTHLY_CAP
    redeemed_count_this_month: int = 0
    first_lesson_required: bool = True


InviteStatus = Literal["pending", "redeemed", "expired", "invalid", "self", "cap_reached"]


class InviteRedeemResponse(BaseModel):
    status: InviteStatus
    lingot_reward: int = 0
    ad_free_minutes: int = 0


# ─── Threads (stub messaging) ────────────────────────────────────────────────


class ThreadItem(BaseModel):
    id: str
    other_user_id: str
    other_username: str
    other_display_name: str
    other_avatar_key: str | None = None
    last_message_preview: str
    last_message_at: datetime
    unread_count: int = 0


class Message(BaseModel):
    id: str
    thread_id: str
    sender_id: str
    body: str
    sent_at: datetime


class ThreadDetailResponse(BaseModel):
    id: str
    other_user_id: str
    other_username: str
    other_display_name: str
    other_avatar_key: str | None = None
    messages: list[Message] = Field(default_factory=list)


class SendMessageBody(BaseModel):
    """Caller payload for POST /threads/{thread_id}/messages."""

    body: str = Field(min_length=1, max_length=4000)


# ─── Friend quest targets ────────────────────────────────────────────────────


class QuestTargetItem(BaseModel):
    user_id: str
    username: str
    display_name: str
    avatar_key: str | None = None
    streak_days: int = 0
    level: int = 1
    reachable_for: list[str] = Field(default_factory=list)


# ─── Friend suggestions ──────────────────────────────────────────────────────


class FriendSuggestionItem(BaseModel):
    """A non-friend, non-blocked candidate to send a friend request to.

    Sourced from the user directory, filtered to users who share the
    requester's ``learning_language`` setting. The ``reason`` is a short
    human label the FE can show next to the suggestion.
    """

    user_id: str
    username: str
    display_name: str
    profile_picture_key: str | None = None
    learning_language: str | None = None
    streak: int = 0
    xp: int = 0
    reason: str = "Same language"


class FriendSuggestionsResponse(BaseModel):
    items: list[FriendSuggestionItem] = Field(default_factory=list)
