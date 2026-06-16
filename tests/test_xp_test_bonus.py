"""Row-test / recap lessons earn a bonus on top of the pass/perfect payout.

Retrieval practice under test conditions is the highest-value rep (the testing
effect), so tests pay a premium over the drills they cap. The client mirror
(``lingo/src/features/progress/xpRules.ts``) already shows this number on the
lesson-complete screen; the server is authoritative and must award the same or
it under-credits what the UI promised.

The bonus is admin-tunable via ``XpEconomyConfig.lesson_test_bonus_xp`` — the
assertions are written against the config defaults so a future re-tune doesn't
silently break them.
"""

import uuid
from datetime import UTC, datetime

from app.platform_settings.schemas import XpEconomyConfig


def _attempt(lesson_id: str, *, score: float) -> dict:
    return {
        "clientAttemptId": str(uuid.uuid4()),
        "lessonId": lesson_id,
        "attemptedAt": datetime.now(UTC).isoformat(),
        "durationSec": 60,
        "passed": True,
        "score": score,
        "stepResults": [
            {"stepIdx": 0, "conceptIds": ["c1"], "correct": True, "durationMs": 4000},
        ],
    }


def _post_one(client, lesson_id: str, *, score: float) -> dict:
    resp = client.post(
        "/api/core/v1/progress/lessons/batch",
        json={"attempts": [_attempt(lesson_id, score=score)], "checkStreak": False},
    )
    assert resp.status_code == 200, resp.text
    [result] = resp.json()["results"]
    assert result["accepted"] is True
    return result


def test_row_test_lesson_earns_bonus(api_client) -> None:
    """A passed (non-perfect) row test pays base + the test bonus."""
    client, _user_id, _ = api_client
    cfg = XpEconomyConfig()
    result = _post_one(client, "m3-row-a-test", score=0.8)
    assert result["xpEarned"] == cfg.lesson_pass_xp + cfg.lesson_test_bonus_xp


def test_recap_lesson_earns_bonus(api_client) -> None:
    """``-recap`` ids get the same premium as ``-test`` ids."""
    client, _user_id, _ = api_client
    cfg = XpEconomyConfig()
    result = _post_one(client, "m3-recap", score=0.8)
    assert result["xpEarned"] == cfg.lesson_pass_xp + cfg.lesson_test_bonus_xp


def test_perfect_row_test_stacks_perfect_and_test_bonus(api_client) -> None:
    """A perfect row test stacks the perfect payout AND the test bonus —
    matching the client's additive formula (10 + 5 + 10 = 25 at defaults)."""
    client, _user_id, _ = api_client
    cfg = XpEconomyConfig()
    result = _post_one(client, "m3-row-a-test", score=1.0)
    assert result["xpEarned"] == cfg.lesson_perfect_xp + cfg.lesson_test_bonus_xp


def test_regular_lesson_has_no_test_bonus(api_client) -> None:
    """A normal lesson id (no -test/-recap suffix) earns only the base payout."""
    client, _user_id, _ = api_client
    cfg = XpEconomyConfig()
    result = _post_one(client, "m3-lesson-01", score=0.8)
    assert result["xpEarned"] == cfg.lesson_pass_xp
