"""Leaderboard read-path tests.

Cost item 5 moved the global leaderboard read off the recompute-from-rollups
path (a ``lingo_users`` Scan + per-user day-rollup fan-out) and onto the
precomputed ``lingo_social_leaderboard`` table that ``lingo-async`` writes on
every opted-in ``xp_awarded`` event. So the global board now reflects the
precomputed bucket, NOT ``progress_day_rollups``.

These tests stage the precomputed table directly (standing in for the async
writer, which doesn't run in the test) and assert the read path surfaces it.
The spotlight ``daily_xp`` array still derives from day-rollups, so the admin
XP-grant → day-rollup write remains exercised by ``test_spotlight_daily_xp_is_array``.
"""

from datetime import UTC, datetime


def _admin_headers() -> dict[str, str]:
    return {"X-Dev-User": "dev|admin-user"}


def _weekly_bucket() -> str:
    iso = datetime.now(UTC).date().isocalendar()
    return f"ja#{iso.year:04d}-W{iso.week:02d}"


def _monthly_bucket() -> str:
    d = datetime.now(UTC).date()
    return f"ja#{d.year:04d}-{d.month:02d}"


async def test_precomputed_weekly_board_surfaces_table_xp(api_client) -> None:
    """A row in the precomputed weekly bucket surfaces with ranked XP +
    hydrated display fields. (No ``lang`` → no concrete bucket → empty.)"""
    client, user_id, _admin_user_id = api_client

    from app.db.provider import get_leaderboard_repo

    repo = get_leaderboard_repo()
    await repo.record_xp(_weekly_bucket(), user_id, 300)

    # Without lang there is no per-language bucket — board is empty.
    no_lang = client.get("/api/core/v1/social/leaderboards/weekly").json()
    assert all(e["user_id"] != user_id for e in no_lang["entries"])

    lb = client.get("/api/core/v1/social/leaderboards/weekly?lang=ja").json()
    me = next((e for e in lb["entries"] if e["user_id"] == user_id), None)
    assert me is not None, "user must appear in the precomputed weekly board"
    assert me["xp_this_period"] == 300
    assert me["rank"] == 1
    assert lb["my_rank"] == 1


async def test_precomputed_monthly_board_surfaces_table_xp(api_client) -> None:
    """Same for the monthly bucket."""
    client, user_id, _admin_user_id = api_client

    from app.db.provider import get_leaderboard_repo

    repo = get_leaderboard_repo()
    await repo.record_xp(_monthly_bucket(), user_id, 150)

    lb = client.get("/api/core/v1/social/leaderboards/monthly?lang=ja").json()
    me = next((e for e in lb["entries"] if e["user_id"] == user_id), None)
    assert me is not None
    assert me["xp_this_period"] == 150


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
