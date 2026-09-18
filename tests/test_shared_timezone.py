"""``app/shared/timezone.py`` — IANA zone validation. Pure unit tests, no
HTTP, no DB. See the module docstring for the design: constructing
``ZoneInfo(candidate)`` IS the validation, so there's no separate allow-list.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest

from app.shared.timezone import DEFAULT_TZ_NAME, resolve_zoneinfo, validate_timezone_name


@pytest.mark.parametrize(
    "raw",
    ["America/Denver", "Asia/Tokyo", "Europe/London", "Pacific/Kiritimati", "UTC"],
)
def test_accepts_real_iana_zones(raw: str) -> None:
    assert validate_timezone_name(raw) == raw


@pytest.mark.parametrize(
    "raw",
    [None, "", "   ", "Mars/Cydonia", "not-a-zone", "A" * 200],
)
def test_falls_back_to_utc_on_garbage(raw: str | None) -> None:
    assert validate_timezone_name(raw) == DEFAULT_TZ_NAME


def test_case_sensitivity_is_filesystem_dependent_not_asserted() -> None:
    """``ZoneInfo("america/denver")`` (lowercase) resolves on this machine
    because zoneinfo falls back to reading tzdata files off a
    case-insensitive filesystem (macOS/APFS) — it would correctly raise
    ``ZoneInfoNotFoundError`` on a case-sensitive filesystem (Linux/Lambda).
    Not asserting either way here — labeling the inconsistency rather than
    pinning environment-dependent behavior as a contract."""


def test_strips_surrounding_whitespace() -> None:
    assert validate_timezone_name("  America/Denver  ") == "America/Denver"


def test_resolve_zoneinfo_returns_real_zoneinfo() -> None:
    tz = resolve_zoneinfo("America/Denver")
    assert isinstance(tz, ZoneInfo)
    assert str(tz) == "America/Denver"


def test_resolve_zoneinfo_falls_back_to_utc() -> None:
    tz = resolve_zoneinfo("Mars/Cydonia")
    assert str(tz) == "UTC"


def test_resolve_zoneinfo_handles_none() -> None:
    assert str(resolve_zoneinfo(None)) == "UTC"
