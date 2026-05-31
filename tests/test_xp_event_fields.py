"""xp_awarded events published from lessons must carry
learning_language_id + leaderboard_opt_in so lingo-async's leaderboard
fan-out doesn't need a user lookup."""

import uuid
from datetime import UTC, datetime
from unittest.mock import patch


def _attempt(client_id: str, lesson_id: str = "lesson-001") -> dict:
    return {
        "clientAttemptId": client_id,
        "lessonId": lesson_id,
        "attemptedAt": datetime.now(UTC).isoformat(),
        "durationSec": 60,
        "passed": True,
        "score": 1.0,
        "stepResults": [
            {"stepIdx": 0, "conceptIds": ["c1"], "correct": True, "durationMs": 4000},
            {"stepIdx": 1, "conceptIds": ["c1"], "correct": True, "durationMs": 5000},
        ],
    }


def test_xp_awarded_event_includes_language_and_optin(api_client) -> None:
    """Smoke: a lesson batch publishes xp_awarded with the new fields populated."""
    client, user_id, _ = api_client

    # Seed a learning language so the event carries a real value.
    resp = client.patch(
        "/api/core/v1/users/me/settings",
        json={"learning": {"learningLanguageId": "ja"}, "social": {"show_on_leaderboard": True}},
    )
    assert resp.status_code == 200, f"settings update failed: {resp.text}"

    published: list[dict] = []

    import app.progress.router as router_mod

    with patch.object(router_mod, "publish_event", side_effect=lambda e: published.append(e)):
        body = {
            "attempts": [_attempt(str(uuid.uuid4()))],
            "checkStreak": False,
        }
        resp = client.post("/api/core/v1/progress/lessons/batch", json=body)
        assert resp.status_code == 200, f"batch failed: {resp.text}"

    xp_events = [e for e in published if e.get("type") == "xp_awarded"]
    assert xp_events, f"expected an xp_awarded event, got {published}"
    e = xp_events[0]
    assert "learning_language_id" in e, f"missing field, event: {e}"
    assert "leaderboard_opt_in" in e, f"missing field, event: {e}"
    assert e["learning_language_id"] == "ja", f"unexpected value: {e}"
    assert e["leaderboard_opt_in"] is True, f"unexpected value: {e}"


def test_xp_awarded_event_defaults_when_no_settings(api_client) -> None:
    """When user has no settings, learning_language_id is None and
    leaderboard_opt_in defaults to True (opt-in by default convention)."""
    client, user_id, _ = api_client

    published: list[dict] = []

    import app.progress.router as router_mod

    with patch.object(router_mod, "publish_event", side_effect=lambda e: published.append(e)):
        body = {
            "attempts": [_attempt(str(uuid.uuid4()))],
            "checkStreak": False,
        }
        resp = client.post("/api/core/v1/progress/lessons/batch", json=body)
        assert resp.status_code == 200, f"batch failed: {resp.text}"

    xp_events = [e for e in published if e.get("type") == "xp_awarded"]
    assert xp_events, f"expected an xp_awarded event, got {published}"
    e = xp_events[0]
    assert "learning_language_id" in e, f"missing field, event: {e}"
    assert "leaderboard_opt_in" in e, f"missing field, event: {e}"
    assert e["learning_language_id"] is None, f"expected None for unset language: {e}"
    assert e["leaderboard_opt_in"] is True, f"expected True default opt-in: {e}"
