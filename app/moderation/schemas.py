"""Pydantic schemas for moderation. Stubs for future implementation."""

from pydantic import BaseModel, Field

# -- Enforcement action (time-based suspension, ban, etc.) --


class EnforcementAction(BaseModel):
    """Time-based enforcement (suspension, ban, mute)."""

    type: str = Field(description="suspension | ban | mute | warn")
    reason: str | None = None
    expires_at: str | None = Field(default=None, description="ISO datetime. null = permanent")


# -- Report (for reporting content or users) --


class ReportCreate(BaseModel):
    """Create a report. Stub."""

    target_type: str = Field(description="story | deck | post | user")
    target_id: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=1000)


class ReportResponse(BaseModel):
    """Report as returned from API. Stub."""

    id: str
    reporter_id: str
    target_type: str
    target_id: str
    reason: str
    status: str = Field(description="open | resolved | dismissed")
    reviewed_by: str | None = None
    created_at: str


# -- Audit log entry --


class ModerationActionLog(BaseModel):
    """Audit log entry for moderator actions."""

    actor_id: str
    action: str
    target_type: str
    target_id: str
    reason: str | None = None
    timestamp: str
