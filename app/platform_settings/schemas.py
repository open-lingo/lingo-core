"""Schemas for platform-settings admin endpoints.

The XP economy config is a fixed-shape Pydantic model so admin UI changes
get validated server-side. New tunables get added here; the underlying
storage (KV with JSON values) doesn't change.

Defaults intentionally mirror the legacy constants in ``app.progress.xp``
so a fresh deploy with an empty settings table behaves identically to
the hardcoded baseline.
"""

from pydantic import BaseModel, Field

# Single key under which the XP economy blob lives in the repo.
XP_ECONOMY_KEY = "xp_economy"


class XpEconomyConfig(BaseModel):
    """Tunable XP / lingot earning rates.

    Field names mirror the original constants in ``app.progress.xp`` so the
    swap-in is mechanical. Keep additions backward-compatible (default
    values + non-breaking renames) — the live config blob may be missing
    newer fields, and Pydantic will fill them in from these defaults.
    """

    lesson_pass_xp: int = Field(default=10, ge=0, le=10_000)
    """Base XP for passing a lesson."""

    lesson_perfect_xp: int = Field(default=15, ge=0, le=10_000)
    """Total XP awarded for a perfect-score pass (NOT a bonus on top of
    lesson_pass_xp — total payout for a perfect attempt).

    The default of 15 = 10 (base) + 5 (legacy perfect bonus), matching the
    old behavior. Admins setting this lower than lesson_pass_xp is a
    misconfig but isn't blocked at the schema level — the progress flow
    just picks whichever is larger when the attempt is perfect.
    """

    review_xp: int = Field(default=2, ge=0, le=10_000)
    """XP per SRS review (placeholder — review-time XP not yet wired)."""

    streak_milestone_xp: int = Field(default=50, ge=0, le=100_000)
    """Bonus XP at each weekly streak milestone."""

    deck_approved_xp: int = Field(default=100, ge=0, le=100_000)
    """XP awarded when a deck the user authored is approved."""

    first_module_finish_xp: int = Field(default=25, ge=0, le=100_000)
    """XP awarded the first time a user finishes every lesson in a module."""

    lingots_per_lesson: int = Field(default=2, ge=0, le=10_000)
    """Lingots awarded per passed lesson."""
