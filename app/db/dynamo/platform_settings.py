"""DynamoDB-backed platform settings repository — stub.

The SQLite implementation is the only working backend today; this stub
raises ``NotImplementedError`` on every operation so the provider can wire
it in without crashing at startup. When it lands, the table will likely
use a single PK (``SETTING#<key>``) with a JSON attribute.
"""

from typing import Any


class DynamoPlatformSettingsRepository:
    def __init__(self, table_name: str, region: str) -> None:
        self._table_name = table_name
        self._region = region

    async def connect(self) -> None:
        # No-op so provider init can succeed; methods raise on use.
        return None

    async def close(self) -> None:
        return None

    async def get(self, key: str) -> dict[str, Any] | None:
        raise NotImplementedError("DynamoPlatformSettingsRepository.get")

    async def put(self, key: str, value: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("DynamoPlatformSettingsRepository.put")
