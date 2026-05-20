"""Pydantic schemas for the progress API.

See ``docs/adr/0001-progress-api-hybrid-rollup.md`` for the data model.
"""

from typing import Literal

from pydantic import BaseModel, Field


# ── Submission ──────────────────────────────────────────────────────────────


class AttemptStepAnswer(BaseModel):
    """User's answer for a single step in a lesson (Phase 2 — server-validated)."""

    stepIdx: int = Field(ge=0)
    choice: str = Field(description="Choice id/value the user picked")


class GradedStepResult(BaseModel):
    """Client-graded step result, used in the Phase 1 batch sync.

    The client knows correct answers (they ship in the lesson bundle today) and
    grades each step as the user finishes it. The batch payload includes these
    already-graded results so the server doesn't have to re-validate per step.
    Phase 2 will move correct answers server-side and replace this with raw
    choice submissions for server validation.
    """

    stepIdx: int = Field(ge=0)
    conceptIds: list[str] = Field(default_factory=list)
    correct: bool
    durationMs: int | None = None


class AttemptSubmission(BaseModel):
    """Body of POST /progress/lessons/:lessonId/attempt (single, dev-convenience)."""

    clientAttemptId: str = Field(
        description="Client-generated UUID for idempotent retries"
    )
    durationSec: int = Field(
        ge=1,
        le=3600,
        description="Total time the user spent on the lesson, clamped server-side",
    )
    answers: list[AttemptStepAnswer] | None = Field(
        default=None,
        description="Phase 2 — raw choices for server-side validation",
    )
    stepResults: list[GradedStepResult] | None = Field(
        default=None,
        description="Phase 1 — client-graded results (current production path)",
    )


class BatchAttempt(BaseModel):
    """One entry in the batch sync payload. Mirrors AttemptSubmission but with
    explicit lesson + timestamp fields (the batch endpoint takes many lessons)."""

    clientAttemptId: str
    lessonId: str
    attemptedAt: str = Field(description="ISO timestamp from the client")
    durationSec: int = Field(ge=1, le=3600)
    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    stepResults: list[GradedStepResult]


class BatchAttemptSubmission(BaseModel):
    """Body of POST /progress/lessons/batch — the main client sync path."""

    attempts: list[BatchAttempt] = Field(min_length=1, max_length=100)


class BatchAttemptResult(BaseModel):
    """Per-attempt response in the batch endpoint."""

    clientAttemptId: str
    attemptId: str | None = None
    accepted: bool
    reason: str | None = Field(
        default=None,
        description="When not accepted: rate-limited / prerequisite-missing / duration-out-of-bounds / etc.",
    )
    xpEarned: int = Field(default=0, ge=0)
    streakAfter: int = Field(default=0, ge=0)
    lingotsEarned: int = Field(default=0, ge=0)
    dailyTotalLessons: int = Field(default=0, ge=0)


class BatchAttemptResponse(BaseModel):
    """Response from POST /progress/lessons/batch."""

    results: list[BatchAttemptResult]


# ── Per-step result returned to client ──────────────────────────────────────


class StepResult(BaseModel):
    stepIdx: int
    correct: bool
    conceptIds: list[str] = Field(
        default_factory=list,
        description="Concepts exercised by this step (informational; do not expose correct_choice)",
    )
    durationMs: int | None = None


class ConceptDelta(BaseModel):
    """How a single concept's mastery changed because of this attempt."""

    conceptId: str
    beforeAccuracy: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Accuracy across all prior attempts (None if first encounter)",
    )
    afterAccuracy: float = Field(ge=0.0, le=1.0)
    change: Literal["improved", "steady", "regressed", "new"]


# ── Attempt response ────────────────────────────────────────────────────────


class AttemptResponse(BaseModel):
    """Response from POST /progress/lessons/:lessonId/attempt."""

    attemptId: str
    lessonId: str
    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    stepResults: list[StepResult]
    conceptDeltas: list[ConceptDelta] = Field(default_factory=list)
    # User-row stat snapshots so the client doesn't have to make a second call
    xpEarned: int = Field(ge=0)
    streakAfter: int = Field(ge=0)
    lingotsEarned: int = Field(default=0, ge=0)
    dailyTotalLessons: int = Field(ge=0)


# ── Read aggregates ─────────────────────────────────────────────────────────


class LessonRollup(BaseModel):
    """Per-lesson eager rollup."""

    lessonId: str
    bestScore: float = Field(ge=0.0, le=1.0)
    firstPassedAt: str | None = None
    latestAttemptAt: str
    attemptCount: int = Field(ge=1)


class ConceptRollup(BaseModel):
    """Per-concept lazy-materialized mastery rollup."""

    conceptId: str
    encounters: int = Field(ge=0)
    correctCount: int = Field(ge=0)
    incorrectCount: int = Field(ge=0)
    recentResults: list[bool] = Field(
        default_factory=list,
        description="Last N results (newest first), used for trend visualization",
    )
    avgDurationMs: int | None = None
    firstSeenAt: str
    lastSeenAt: str
    lastCorrectAt: str | None = None


class DayActivity(BaseModel):
    """Per-day rollup for the home-page sparkline + streak."""

    date: str = Field(description="YYYY-MM-DD")
    lessonsCompleted: int = Field(ge=0)
    minutesActive: int = Field(ge=0)
    xpEarned: int = Field(ge=0)


class UserStats(BaseModel):
    """Hot stats lifted from the user row. Used by the home header chrome."""

    streak: int = Field(ge=0)
    bestStreak: int = Field(ge=0)
    lastActiveDate: str | None = None
    xp: int = Field(ge=0)
    level: int = Field(ge=1)
    lingots: int = Field(ge=0)


class ProgressSummary(BaseModel):
    """Aggregate returned from GET /progress/me — one-shot page render payload."""

    user: UserStats
    lessons: list[LessonRollup]
    concepts: list[ConceptRollup]
    last30days: list[DayActivity]


# ── Recent attempts feed ────────────────────────────────────────────────────


class AttemptSummary(BaseModel):
    """Lightweight attempt entry for history/feed views."""

    attemptId: str
    lessonId: str
    attemptedAt: str
    durationSec: int
    passed: bool
    score: float


class AttemptList(BaseModel):
    items: list[AttemptSummary]
    nextCursor: str | None = None


# ── Touch endpoint ──────────────────────────────────────────────────────────


class TouchResponse(BaseModel):
    """Returned by POST /progress/me/touch — login/session-start hook."""

    user: UserStats
    streakUpdated: bool = Field(
        description="True if today is the first activity day for the user"
    )
    staleConceptIds: list[str] = Field(
        default_factory=list,
        description="Concepts that were flagged stale at this touch — frontend can prefetch on next read",
    )
