"""Recurring quest catalog: deterministic pool picks + reset boundaries.

Pure unit tests against the generator functions (no HTTP, no DB) — see
``app/quests/router.py``'s ``_daily_catalog`` / ``_weekly_catalog`` /
``_seeded_pick`` docstrings for the contract these enforce:

  - same user + same calendar day/ISO week -> same picks, every call
    (two devices reading independently must agree without talking to
    each other)
  - different day/week -> a fresh id namespace (old rows don't collide
    with the new picks)
  - daily resets at UTC midnight; weekly resets at UTC Monday 00:00
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.quests.router import (
    _DAILY_PICK_COUNT,
    _DAILY_POOL,
    _WEEKLY_PICK_COUNT,
    _WEEKLY_POOL,
    _daily_catalog,
    _default_catalog,
    _weekly_catalog,
)

_UID_A = "auth0|catalog-a"
_UID_B = "auth0|catalog-b"


def test_daily_catalog_picks_a_fixed_count() -> None:
    now = datetime(2026, 9, 18, 15, 30, tzinfo=UTC)
    rows = _daily_catalog(_UID_A, now)
    assert len(rows) == _DAILY_PICK_COUNT == 3
    assert {r["type"] for r in rows} == {"daily"}
    # Every pick is a real pool entry (by title_key) and no duplicates.
    titles = [r["title_key"] for r in rows]
    assert len(set(titles)) == len(titles)
    pool_titles = {p["title_key"] for p in _DAILY_POOL}
    assert set(titles) <= pool_titles


def test_weekly_catalog_picks_a_fixed_count() -> None:
    now = datetime(2026, 9, 18, 15, 30, tzinfo=UTC)
    rows = _weekly_catalog(_UID_A, now)
    assert len(rows) == _WEEKLY_PICK_COUNT == 2
    assert {r["type"] for r in rows} == {"weekly"}
    titles = [r["title_key"] for r in rows]
    assert len(set(titles)) == len(titles)
    pool_titles = {p["title_key"] for p in _WEEKLY_POOL}
    assert set(titles) <= pool_titles


def test_same_user_same_day_is_deterministic() -> None:
    """Two independent calls (== two devices reading the same day) agree."""
    now = datetime(2026, 9, 18, 9, 0, tzinfo=UTC)
    first = _daily_catalog(_UID_A, now)
    second = _daily_catalog(_UID_A, now)
    assert [r["id"] for r in first] == [r["id"] for r in second]

    # Time-of-day within the same calendar day must not change the pick.
    later = datetime(2026, 9, 18, 23, 59, tzinfo=UTC)
    third = _daily_catalog(_UID_A, later)
    assert [r["id"] for r in first] == [r["id"] for r in third]


def test_same_user_same_week_is_deterministic() -> None:
    monday = datetime(2026, 9, 14, 1, 0, tzinfo=UTC)  # ISO week start
    sunday = datetime(2026, 9, 20, 23, 0, tzinfo=UTC)  # same ISO week
    a = _weekly_catalog(_UID_A, monday)
    b = _weekly_catalog(_UID_A, sunday)
    assert [r["id"] for r in a] == [r["id"] for r in b]


def test_different_users_can_diverge() -> None:
    """Not a hard guarantee for every seed, but this pair is known to
    diverge — regression guard against a seed that ignores user_id."""
    now = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
    a = {r["title_key"] for r in _daily_catalog(_UID_A, now)}
    b = {r["title_key"] for r in _daily_catalog(_UID_B, now)}
    assert a != b


def test_different_day_changes_the_id_namespace() -> None:
    day1 = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
    day2 = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
    ids1 = {r["id"] for r in _daily_catalog(_UID_A, day1)}
    ids2 = {r["id"] for r in _daily_catalog(_UID_A, day2)}
    assert ids1.isdisjoint(ids2)


def test_daily_expires_at_utc_midnight() -> None:
    now = datetime(2026, 9, 18, 15, 30, tzinfo=UTC)
    rows = _daily_catalog(_UID_A, now)
    for r in rows:
        expires = datetime.fromisoformat(r["expires_at"])
        assert expires == datetime(2026, 9, 19, 0, 0, tzinfo=UTC)


def test_weekly_expires_at_next_utc_monday() -> None:
    # Wednesday 2026-09-16 -> next Monday is 2026-09-21.
    wednesday = datetime(2026, 9, 16, 10, 0, tzinfo=UTC)
    rows = _weekly_catalog(_UID_A, wednesday)
    for r in rows:
        expires = datetime.fromisoformat(r["expires_at"])
        assert expires == datetime(2026, 9, 21, 0, 0, tzinfo=UTC)


def test_weekly_expires_a_full_week_out_on_monday_itself() -> None:
    # If "now" IS Monday, the reset is 7 days out, not today.
    monday = datetime(2026, 9, 14, 3, 0, tzinfo=UTC)
    rows = _weekly_catalog(_UID_A, monday)
    for r in rows:
        expires = datetime.fromisoformat(r["expires_at"])
        assert expires == datetime(2026, 9, 21, 0, 0, tzinfo=UTC)


def test_default_catalog_combines_both_buckets() -> None:
    now = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
    rows = _default_catalog(_UID_A, now)
    assert len(rows) == _DAILY_PICK_COUNT + _WEEKLY_PICK_COUNT
    assert sum(1 for r in rows if r["type"] == "daily") == _DAILY_PICK_COUNT
    assert sum(1 for r in rows if r["type"] == "weekly") == _WEEKLY_PICK_COUNT


def test_pool_entries_have_positive_rewards() -> None:
    """Every pool item pays something — a claim that grants nothing would
    be a silent dead end, not a bonus."""
    for pool in (_DAILY_POOL, _WEEKLY_POOL):
        for entry in pool:
            assert entry.get("reward_xp") or entry.get("reward_lingots")
            assert entry["progress_target"] > 0
            assert entry["progress_unit"] in ("XP", "lessons", "cards")
