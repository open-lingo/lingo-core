"""XP and lingot earning rules.

Tunable here without DB schema changes. Keep server-authoritative — clients
do not compute XP; they receive the result from POST /attempt.

See ADR-0001 for the broader design context.
"""

# Base XP per lesson completion (passed)
XP_LESSON_COMPLETE = 10

# Bonus XP when the user passes a lesson with a perfect score
XP_PERFECT_BONUS = 5

# XP at each weekly streak milestone (every 7 consecutive days)
XP_WEEKLY_STREAK_BONUS = 50

# XP awarded when a deck the user authored is approved for community publication
XP_DECK_APPROVED = 100

# XP awarded the first time a user finishes every lesson in a module
XP_FIRST_MODULE_FINISH = 25

# How many XP points map to one level. Linear curve; we may switch to a
# tuned curve later, but linear is fine for MVP gamification.
XP_PER_LEVEL = 500


# Lingots — in-app currency (post-MVP cosmetics economy, schema-ready)
LINGOTS_LESSON_COMPLETE = 2
LINGOTS_DECK_APPROVED = 20
LINGOTS_WEEKLY_STREAK = 10


def level_for_xp(xp: int) -> int:
    """Linear leveling. Level 1 starts at xp=0, level 2 at xp=500, etc."""
    return max(1, xp // XP_PER_LEVEL + 1)


def xp_for_attempt(passed: bool, score: float) -> int:
    """Compute the XP a single attempt awards. Returns 0 if not passed."""
    if not passed:
        return 0
    base = XP_LESSON_COMPLETE
    if score >= 0.999:
        base += XP_PERFECT_BONUS
    return base


def lingots_for_attempt(passed: bool) -> int:
    return LINGOTS_LESSON_COMPLETE if passed else 0
