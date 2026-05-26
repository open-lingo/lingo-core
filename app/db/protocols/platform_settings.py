"""PlatformSettingsRepository protocol.

Backs the admin-tunable platform configuration — XP economy values today,
extensible to other knobs (rate limits, feature flags, default streak
freezes…) as they land.

Storage shape is intentionally a simple key/value store with JSON-encoded
payloads so new settings groups can be added without schema migrations.
Each key (e.g. ``"xp_economy"``) holds an arbitrary JSON object.
"""

from typing import Any, Protocol


class PlatformSettingsRepository(Protocol):
    """Tiny KV store for admin-tunable configuration."""

    async def get(self, key: str) -> dict[str, Any] | None:
        """Return the JSON object stored at ``key``, or None when absent."""
        ...

    async def put(self, key: str, value: dict[str, Any]) -> dict[str, Any]:
        """Upsert ``value`` at ``key``. Returns the stored object."""
        ...
