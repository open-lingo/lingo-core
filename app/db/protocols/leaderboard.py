"""LeaderboardRepository protocol.

Read side of the precomputed ``lingo_social_leaderboard`` table that
``lingo-async`` maintains (one ``ADD xp`` UpdateItem per opted-in XP event,
partitioned by language + period). lingo-core used to recompute leaderboards
on every ``/social/leaderboards/*`` call by Scanning ``lingo_users`` and
fanning out a per-user day-rollup Query storm; this protocol replaces that hot
path with bounded reads against the rollup table.

Bucket-string scheme (matches ``lingo-async/app/leaderboard/updater.py`` and
``lingo-infra/main.tf``):
  weekly:  ``{lang}#{YYYY}-W{ww}``      e.g. ``ja#2026-W21``
  monthly: ``{lang}#{YYYY}-{MM}``       e.g. ``ja#2026-06``

Item layout: ``PK = BUCKET#<bucket>``, ``SK = USER#<user_id>``, ``xp`` (N).
``top_n`` requires the GSI (``GSI1PK = PK``, ``GSI1SK = xp``) so the top of a
bucket is a single bounded Query instead of read-whole-bucket + sort-in-Lambda.
"""

from typing import Any, Protocol


class LeaderboardRepository(Protocol):
    async def top_n(self, bucket: str, limit: int) -> list[dict[str, Any]]:
        """Top ``limit`` rows for ``bucket`` (the bucket string, no ``BUCKET#``
        prefix), highest XP first. Each row: ``{"user_id": str, "xp": int}``."""
        ...

    async def get_entry(self, bucket: str, user_id: str) -> dict[str, Any] | None:
        """The caller's own row in ``bucket``: ``{"user_id", "xp"}`` or None."""
        ...

    async def rank_for_xp(self, bucket: str, xp: int) -> int:
        """1-based rank for a user holding ``xp`` in ``bucket`` — count of rows
        with strictly greater XP, plus one. ``xp <= 0`` callers may skip."""
        ...

    async def bucket_size(self, bucket: str) -> int:
        """Total number of ranked rows in ``bucket`` (for the board ``total``)."""
        ...
