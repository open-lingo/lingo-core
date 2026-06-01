"""Regression test: admin-awarded XP must appear in the weekly/monthly
leaderboard, not just on user.xp.

Root cause that this test guards against:
  ``_build_leaderboard`` reads ``progress_day_rollups`` (via
  ``_xp_in_window``). Before this fix, admin XP grants only wrote
  ``user.xp`` and silently skipped ``progress_day_rollups``, so the
  leaderboard showed 0 XP for the target even after the award.
"""


def _admin_headers() -> dict[str, str]:
    return {"X-Dev-User": "dev|admin-user"}


def test_award_xp_appears_in_weekly_leaderboard(api_client, monkeypatch) -> None:
    """After awarding XP, the target user's ``xp_this_period`` in the weekly
    leaderboard must reflect the award (was: always 0)."""
    client, user_id, admin_user_id = api_client

    from app.config import settings as cfg

    monkeypatch.setattr(cfg, "ADMIN_USER_IDS", [admin_user_id])

    # Sanity: user starts at 0 on the leaderboard.
    lb_before = client.get("/api/core/v1/social/leaderboards/weekly").json()
    me_before = next(
        (e for e in lb_before["entries"] if e["user_id"] == user_id), None
    )
    assert me_before is not None, "user must appear in weekly leaderboard"
    assert me_before["xp_this_period"] == 0, "expected 0 XP before award"

    # Award XP via admin endpoint.
    resp = client.post(
        f"/api/core/v1/admin/users/{user_id}/award-xp",
        json={"amount": 300, "reason": "regression test"},
        headers=_admin_headers(),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["xp"] == 300

    # Leaderboard must now show the awarded amount.
    lb_after = client.get("/api/core/v1/social/leaderboards/weekly").json()
    me_after = next(
        (e for e in lb_after["entries"] if e["user_id"] == user_id), None
    )
    assert me_after is not None, "user must still appear in weekly leaderboard"
    assert me_after["xp_this_period"] == 300, (
        f"expected 300 XP after award, got {me_after['xp_this_period']} — "
        "admin XP grant did not write progress_day_rollups"
    )
    # Rank should be first (only user with non-zero XP in the test DB).
    assert me_after["rank"] == 1


def test_award_xp_appears_in_monthly_leaderboard(api_client, monkeypatch) -> None:
    """Same regression for the monthly board."""
    client, user_id, admin_user_id = api_client

    from app.config import settings as cfg

    monkeypatch.setattr(cfg, "ADMIN_USER_IDS", [admin_user_id])

    resp = client.post(
        f"/api/core/v1/admin/users/{user_id}/award-xp",
        json={"amount": 150, "reason": "monthly regression"},
        headers=_admin_headers(),
    )
    assert resp.status_code == 200, resp.text

    lb = client.get("/api/core/v1/social/leaderboards/monthly").json()
    me = next((e for e in lb["entries"] if e["user_id"] == user_id), None)
    assert me is not None
    assert me["xp_this_period"] == 150, (
        f"expected 150 XP, got {me['xp_this_period']}"
    )


def test_spotlight_daily_xp_is_array(api_client, monkeypatch) -> None:
    """Spotlight daily_xp and friend_median_daily_xp must be arrays (7 ints),
    not scalars. The FE adapter guards Array.isArray() so a scalar would
    silently produce an empty chart."""
    client, user_id, admin_user_id = api_client

    from app.config import settings as cfg

    monkeypatch.setattr(cfg, "ADMIN_USER_IDS", [admin_user_id])

    # Award some XP so today's slot is non-zero.
    client.post(
        f"/api/core/v1/admin/users/{user_id}/award-xp",
        json={"amount": 100, "reason": "spotlight test"},
        headers=_admin_headers(),
    )

    spot = client.get("/api/core/v1/social/leaderboards/spotlight").json()
    assert isinstance(spot["daily_xp"], list), (
        f"daily_xp must be a list, got {type(spot['daily_xp'])}"
    )
    assert isinstance(spot["friend_median_daily_xp"], list), (
        f"friend_median_daily_xp must be a list, got {type(spot['friend_median_daily_xp'])}"
    )
    assert len(spot["daily_xp"]) == 7, (
        f"expected 7-element daily_xp, got {len(spot['daily_xp'])}"
    )
    assert len(spot["friend_median_daily_xp"]) == 7
    # Today (index 6) must reflect the award.
    assert spot["daily_xp"][6] == 100, (
        f"today's XP should be 100, got {spot['daily_xp'][6]}"
    )
