"""Timezone-change claim idempotency — the edge rule from the
quest-timezone lane: a quest claimed for local day D in zone A must stay
claimed if the same local date string D recurs under zone B, even though
real (UTC) time has moved past the zone-A bucket's ``expires_at``.

Reproduces the concrete failure mode: a daily quest is completed+claimed
while the device is in America/Denver. The device then travels to a zone
far enough west (Pacific/Honolulu, UTC-10) that its LOCAL calendar date is
STILL the same string even after the Denver bucket's real-time
``expires_at`` has passed — ``_daily_catalog``'s seed (user_id + local day)
is a pure function of that date string, so it regenerates the EXACT SAME
row id. Without the ``protected_rows`` guard in ``_ensure_active_catalog``,
the ordinary expiry sweep would delete the completed row and
``put_quest`` a fresh active/zero-progress copy under that same id —
letting the user complete and claim it a second time.
"""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest_asyncio

from app.db.sqlite.quests import SqliteQuestRepository
from app.quests.router import _daily_catalog, _ensure_active_catalog

_DENVER = ZoneInfo("America/Denver")
_HONOLULU = ZoneInfo("Pacific/Honolulu")  # UTC-10, no DST
_UID = "auth0|tz-collision-user"


@pytest_asyncio.fixture()
async def repo():
    path = os.path.join(tempfile.mkdtemp(prefix="lingo-quests-tz-"), "q.db")
    r = SqliteQuestRepository(path)
    await r.connect()
    try:
        yield r
    finally:
        await r.close()


def _find_collision_instant() -> tuple[datetime, datetime]:
    """Return (mint_now, later_now) where:

    - at ``mint_now`` (Denver-local), today's Denver daily catalog has
      some day_key D and expires at Denver-local midnight (an absolute
      UTC instant E);
    - at ``later_now`` (real UTC time > E, i.e. the Denver bucket has
      truly expired), the Honolulu-local calendar date is STILL D.
    """
    # 2026-09-18 20:00 MDT (Denver, UTC-6) = 2026-09-19 02:00 UTC.
    # Denver day_key = "2026-09-18"; expires at Denver midnight
    # 2026-09-19 00:00 MDT = 2026-09-19 06:00 UTC.
    mint_now = datetime(2026, 9, 19, 2, 0, tzinfo=UTC)
    assert mint_now.astimezone(_DENVER).date().isoformat() == "2026-09-18"

    # Pick a later real instant PAST the Denver expiry (06:00 UTC on the
    # 19th) where Honolulu (UTC-10) local date is still "2026-09-18".
    # 2026-09-19 07:00 UTC -> Honolulu 2026-09-18 21:00 -- still the 18th.
    later_now = datetime(2026, 9, 19, 7, 0, tzinfo=UTC)
    assert later_now > mint_now
    assert later_now.astimezone(_HONOLULU).date().isoformat() == "2026-09-18"
    return mint_now, later_now


async def test_completed_quest_survives_a_timezone_hop_that_recreates_its_id(
    repo: SqliteQuestRepository,
) -> None:
    mint_now, later_now = _find_collision_instant()

    # Mint + immediately complete+claim one Denver daily quest.
    denver_rows = _daily_catalog(_UID, mint_now, _DENVER)
    target = denver_rows[0]
    target["user_id"] = _UID
    target["status"] = "completed"
    target["progress_current"] = target["progress_target"]
    target["reward_granted"] = True
    target["created_at"] = mint_now.isoformat()
    await repo.put_quest(target)
    other_two_ids = {r["id"] for r in denver_rows[1:]}

    # Device hops to Honolulu. A later /quests call (past the Denver
    # bucket's real expiry) must NOT reset the completed quest.
    result_rows = await _ensure_active_catalog(repo, _UID, tz=_HONOLULU, now=later_now)
    by_id = {r["id"]: r for r in result_rows}

    assert target["id"] in by_id, "the completed row must not vanish"
    restored = by_id[target["id"]]
    assert restored["status"] == "completed", "a timezone hop that recreates an already-claimed quest's id must NOT reset it to active/unclaimed — that's a double-award path"
    assert restored["progress_current"] == target["progress_target"]
    assert restored["reward_granted"] is True

    # The claim path itself is independently exactly-once (test_quests_
    # claim_race.py) — re-asserting the row's claimed state here is the
    # relevant guarantee: claim_quest() 409s/no-ops on a "completed" row.
    _ = other_two_ids  # the other two original picks are allowed to sweep


async def test_a_genuinely_new_local_day_still_resets_normally(
    repo: SqliteQuestRepository,
) -> None:
    """Sanity check the guard doesn't over-fire: a real day rollover (no
    collision) still sweeps + reseeds exactly as before."""
    mint_now, _ = _find_collision_instant()
    denver_rows = _daily_catalog(_UID, mint_now, _DENVER)
    target = denver_rows[0]
    target["user_id"] = _UID
    target["status"] = "completed"
    target["progress_current"] = target["progress_target"]
    target["reward_granted"] = True
    target["created_at"] = mint_now.isoformat()
    await repo.put_quest(target)

    # A full 2 real days later, still in Denver — genuinely a new day_key,
    # no collision at all.
    much_later = datetime(2026, 9, 21, 15, 0, tzinfo=UTC)
    result_rows = await _ensure_active_catalog(repo, _UID, tz=_DENVER, now=much_later)
    ids = {r["id"] for r in result_rows}

    assert target["id"] not in ids, "the stale old-day row should have been swept"
    assert any(r["type"] == "daily" and r["status"] == "active" for r in result_rows)
