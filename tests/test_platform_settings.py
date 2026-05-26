"""Platform-settings admin endpoints + XP-config integration with progress.

Verifies:
  - GET defaults when nothing has been stored (Pydantic fills in defaults).
  - PUT persists the new config and a subsequent GET reads it back.
  - The /progress/lessons/batch sync uses the configured XP value
    (not the legacy hardcoded constant).
  - Non-admin callers are 403'd on both routes.
"""

import uuid
from datetime import UTC, datetime


def _attempt(client_id: str, passed: bool = True, score: float = 0.5) -> dict:
    """A minimal accepted attempt — score < 0.999 so the non-perfect XP
    constant applies."""
    return {
        "clientAttemptId": client_id,
        "lessonId": "lesson-cfg",
        "attemptedAt": datetime.now(UTC).isoformat(),
        "durationSec": 60,
        "passed": passed,
        "score": score,
        "stepResults": [
            {"stepIdx": 0, "conceptIds": ["c1"], "correct": True, "durationMs": 4000},
        ],
    }


def _admin_headers() -> dict[str, str]:
    return {"X-Dev-User": "dev|admin-user"}


def test_get_xp_returns_defaults_when_unseeded(api_client, monkeypatch) -> None:
    client, _user_id, admin_user_id = api_client

    from app.config import settings as cfg

    monkeypatch.setattr(cfg, "ADMIN_USER_IDS", [admin_user_id])

    resp = client.get(
        "/api/core/v1/admin/platform-settings/xp",
        headers=_admin_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Default values match the legacy XP constants. lesson_pass_xp=10 is the
    # ground truth for the migration; if this changes, also update the
    # default in app/platform_settings/schemas.py.
    assert body["lesson_pass_xp"] == 10
    assert body["lesson_perfect_xp"] == 15
    assert body["review_xp"] == 2
    assert body["lingots_per_lesson"] == 2


def test_put_then_get_xp_roundtrips(api_client, monkeypatch) -> None:
    client, _user_id, admin_user_id = api_client

    from app.config import settings as cfg

    monkeypatch.setattr(cfg, "ADMIN_USER_IDS", [admin_user_id])

    payload = {
        "lesson_pass_xp": 25,
        "lesson_perfect_xp": 50,
        "review_xp": 5,
        "streak_milestone_xp": 200,
        "deck_approved_xp": 500,
        "first_module_finish_xp": 75,
        "lingots_per_lesson": 4,
    }
    put_resp = client.put(
        "/api/core/v1/admin/platform-settings/xp",
        json=payload,
        headers=_admin_headers(),
    )
    assert put_resp.status_code == 200, put_resp.text
    assert put_resp.json()["lesson_pass_xp"] == 25

    get_resp = client.get(
        "/api/core/v1/admin/platform-settings/xp",
        headers=_admin_headers(),
    )
    assert get_resp.status_code == 200, get_resp.text
    assert get_resp.json() == payload


def test_non_admin_blocked_on_get(api_client) -> None:
    client, _user_id, _admin_user_id = api_client
    resp = client.get("/api/core/v1/admin/platform-settings/xp")
    assert resp.status_code == 403, resp.text


def test_non_admin_blocked_on_put(api_client) -> None:
    client, _user_id, _admin_user_id = api_client
    resp = client.put(
        "/api/core/v1/admin/platform-settings/xp",
        json={"lesson_pass_xp": 999},
    )
    assert resp.status_code == 403, resp.text


def test_progress_batch_uses_configured_xp(api_client, monkeypatch) -> None:
    """End-to-end: admin sets XP=42 → lesson sync awards 42 XP per pass."""
    client, _user_id, admin_user_id = api_client

    from app.config import settings as cfg

    monkeypatch.setattr(cfg, "ADMIN_USER_IDS", [admin_user_id])

    # Seed an unusual lesson_pass_xp value so we can't accidentally match
    # the default. lesson_perfect_xp is forced equal so a perfect-score
    # attempt also lands on 42 (lets us reuse the same XP value across
    # both score paths).
    put_resp = client.put(
        "/api/core/v1/admin/platform-settings/xp",
        json={
            "lesson_pass_xp": 42,
            "lesson_perfect_xp": 42,
            "review_xp": 2,
            "streak_milestone_xp": 50,
            "deck_approved_xp": 100,
            "first_module_finish_xp": 25,
            "lingots_per_lesson": 7,
        },
        headers=_admin_headers(),
    )
    assert put_resp.status_code == 200, put_resp.text

    # Submit an attempt as the dev user — should now earn 42 XP, 7 lingots.
    cid = str(uuid.uuid4())
    body = {"attempts": [_attempt(cid, passed=True, score=0.5)], "checkStreak": False}
    resp = client.post("/api/core/v1/progress/lessons/batch", json=body)
    assert resp.status_code == 200, resp.text
    result = resp.json()["results"][0]
    assert result["accepted"] is True
    assert result["xpEarned"] == 42
    assert result["lingotsEarned"] == 7

    # And the user-row reflects the same totals.
    me = client.get("/api/core/v1/users/me").json()
    assert me["xp"] == 42
