"""Streak-freeze consumption.

A purchased streak-freeze (shop consumable) must bridge a gap day instead of
letting the streak reset to 1 — otherwise the item is sold but never honored.
One freeze is spent per missed day; if the user can't cover the whole gap the
streak resets and no freezes are burned (we don't waste a partial stash).

The router reads the user row + settings blob and writes the decremented
inventory. We stub those repo reads (a user with a multi-day gap + a freeze
inventory) and capture the write, which keeps the whole flow on the
TestClient's event loop and avoids cross-loop access to the live SQLite
connection. ``_missed_days`` is also covered as a pure function.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

from app.progress.router import _missed_days


def _attempt() -> dict:
    return {
        "clientAttemptId": str(uuid.uuid4()),
        "lessonId": "m1-lesson-01",
        "attemptedAt": datetime.now(UTC).isoformat(),
        "durationSec": 60,
        "passed": True,
        "score": 0.8,
        "stepResults": [
            {"stepIdx": 0, "conceptIds": ["c1"], "correct": True, "durationMs": 4000},
        ],
    }


def test_missed_days_pure() -> None:
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    three_ago = (date.today() - timedelta(days=3)).isoformat()
    future = (date.today() + timedelta(days=2)).isoformat()
    assert _missed_days(today, today) == 0  # same day → nothing missed
    assert _missed_days(yesterday, today) == 0  # consecutive → no gap
    assert _missed_days(three_ago, today) == 2  # two whole days missed
    assert _missed_days(None, today) == 0  # never active
    assert _missed_days("not-a-date", today) == 0  # garbage
    assert _missed_days(future, today) == 0  # clock skew → no negative


def _stub_user_repo(api_client, monkeypatch, *, days_ago: int, streak: int, freezes: int):
    """Stub the user repo so the acting user looks like they have a `days_ago`
    gap and `freezes` streak-freezes. Returns (client, captured_settings_writes)."""
    client, _user_id, _ = api_client
    from app.db import provider

    repo = provider.get_user_repo()
    last_active = (date.today() - timedelta(days=days_ago)).isoformat()

    async def fake_get_user_by_id(_uid):
        # No ban fields → passes get_registered_user's ban check.
        return {
            "id": "stub",
            "xp": 0,
            "lingots": 0,
            "streak": streak,
            "best_streak": streak,
            "last_active_date": last_active,
        }

    async def fake_get_settings(_uid):
        return {"shop": {"inventory": {"streak-freeze": freezes}}}

    writes: list[dict] = []

    async def capture_update_settings(_uid, patch):
        writes.append(patch)
        return patch

    async def noop_update_user(_uid, patch):
        return patch

    monkeypatch.setattr(repo, "get_user_by_id", fake_get_user_by_id)
    monkeypatch.setattr(repo, "get_settings", fake_get_settings)
    monkeypatch.setattr(repo, "update_settings", capture_update_settings)
    monkeypatch.setattr(repo, "update_user", noop_update_user)
    return client, writes


def _submit(client) -> dict:
    resp = client.post(
        "/api/core/v1/progress/lessons/batch",
        json={"attempts": [_attempt()], "checkStreak": True},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


def test_streak_freeze_bridges_gap(api_client, monkeypatch) -> None:
    """Two missed days + two freezes → streak continues and both are spent."""
    client, writes = _stub_user_repo(
        api_client, monkeypatch, days_ago=3, streak=5, freezes=2
    )
    result = _submit(client)
    assert result["streakAfter"] == 6, "streak should continue, bridged by freezes"
    shop_writes = [w for w in writes if "shop" in w]
    assert shop_writes, "expected an inventory write"
    assert shop_writes[-1]["shop"]["inventory"]["streak-freeze"] == 0


def test_streak_resets_when_not_enough_freezes(api_client, monkeypatch) -> None:
    """Two missed days but only one freeze → reset to 1, freeze left untouched."""
    client, writes = _stub_user_repo(
        api_client, monkeypatch, days_ago=3, streak=5, freezes=1
    )
    result = _submit(client)
    assert result["streakAfter"] == 1, "streak should reset when the gap isn't covered"
    assert [w for w in writes if "shop" in w] == [], "freeze must not be partially burned"


def test_consecutive_day_consumes_no_freeze(api_client, monkeypatch) -> None:
    """Active yesterday (no gap) → normal +1, no freeze spent even if owned."""
    client, writes = _stub_user_repo(
        api_client, monkeypatch, days_ago=1, streak=5, freezes=2
    )
    result = _submit(client)
    assert result["streakAfter"] == 6
    assert [w for w in writes if "shop" in w] == [], "no freeze on a consecutive day"
