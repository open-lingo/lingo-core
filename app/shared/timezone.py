"""IANA timezone name validation — shared by the auth layer (parsing the
client's ``X-Lingo-Timezone`` header) and the quest catalog (bucketing
daily/weekly resets by the user's LOCAL calendar day, see
``app/quests/router.py``).

Decision (2026-09-18, quest-timezone lane): the client sends its device's
IANA zone on every authenticated request; the server validates it against
the real tzdata database and falls back to UTC for anything garbage,
missing, or absurdly long. There is no separate list of "valid" zone names
to keep in sync with tzdata — constructing ``ZoneInfo(candidate)`` IS the
validation (it raises for anything that isn't a real key).
"""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TZ_NAME = "UTC"

# A real IANA key is short (longest is "America/Argentina/ComodRivadavia"
# at 34 chars); 100 is a generous ceiling that still rejects an obvious
# header-stuffing attempt before it ever reaches ZoneInfo().
_MAX_TZ_NAME_LEN = 100


def validate_timezone_name(raw: str | None) -> str:
    """Return ``raw`` if it's a real IANA zone key, else ``"UTC"``.

    Never raises. Empty/missing/oversized/garbage input all fall back to
    UTC rather than erroring the request — a bad timezone header must
    never be the reason an otherwise-valid request fails.
    """
    if not raw:
        return DEFAULT_TZ_NAME
    candidate = raw.strip()
    if not candidate or len(candidate) > _MAX_TZ_NAME_LEN:
        return DEFAULT_TZ_NAME
    try:
        ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return DEFAULT_TZ_NAME
    return candidate


def resolve_zoneinfo(tz_name: str | None) -> ZoneInfo:
    """Validated ``ZoneInfo`` for ``tz_name``, defaulting to UTC.

    Re-validates even for values already stored on a user row (cheap,
    defensive — guards against stale/bad data written before this
    validation existed, or a future data-format change) rather than
    trusting the caller.
    """
    return ZoneInfo(validate_timezone_name(tz_name))
