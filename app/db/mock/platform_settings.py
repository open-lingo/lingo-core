"""In-memory mock platform settings repository.

Used in production until ``DynamoPlatformSettingsRepository`` lands.
Implements the PlatformSettingsRepository protocol: get / put against
a process-local dict.

Data resets on Lambda cold start — same trade-off as the other Mock
repos. Settings written through this impl will revert to defaults
when the container recycles, which is acceptable as a stop-gap so
the admin UI loads without 503ing every render.
"""

from copy import deepcopy
from typing import Any


class MockPlatformSettingsRepository:
    """In-memory implementation of PlatformSettingsRepository."""

    def __init__(self) -> None:
        self._kv: dict[str, dict[str, Any]] = {}

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def get(self, key: str) -> dict[str, Any] | None:
        v = self._kv.get(key)
        return deepcopy(v) if v else None

    async def put(self, key: str, value: dict[str, Any]) -> dict[str, Any]:
        self._kv[key] = dict(value)
        return deepcopy(value)
