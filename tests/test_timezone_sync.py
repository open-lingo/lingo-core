"""``X-Lingo-Timezone`` header parsing + last-write-wins persistence onto
the user row (`app/auth/dependencies.py::sync_request_timezone`, wired
into `GET /boot` — see that router's `get_boot` for why boot, not every
authenticated route). See `app/shared/timezone.py` for validation and
`app/quests/router.py` for the consumer (daily/weekly quest bucketing by
local calendar day).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.db.sqlite.user import SqliteUserRepository

_BOOT = "/api/core/v1/boot"


async def _read_user(tmp_db_path: str, user_id: str) -> dict[str, Any]:
    repo = SqliteUserRepository(tmp_db_path)
    await repo.connect()
    try:
        record = await repo.get_user_by_id(user_id)
        assert record is not None
        return record
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_valid_timezone_header_is_persisted(api_client, tmp_db_path: str) -> None:
    client, user_id, _admin_id = api_client
    resp = client.get(_BOOT, headers={"X-Lingo-Timezone": "America/Denver"})
    assert resp.status_code == 200

    record = await _read_user(tmp_db_path, user_id)
    assert record["timezone"] == "America/Denver"


@pytest.mark.asyncio
async def test_missing_header_defaults_to_utc(api_client, tmp_db_path: str) -> None:
    client, user_id, _admin_id = api_client
    resp = client.get(_BOOT)
    assert resp.status_code == 200

    record = await _read_user(tmp_db_path, user_id)
    assert record["timezone"] == "UTC"


@pytest.mark.asyncio
async def test_garbage_header_falls_back_to_utc(api_client, tmp_db_path: str) -> None:
    client, user_id, _admin_id = api_client
    resp = client.get(_BOOT, headers={"X-Lingo-Timezone": "Mars/Cydonia"})
    assert resp.status_code == 200

    record = await _read_user(tmp_db_path, user_id)
    assert record["timezone"] == "UTC"


@pytest.mark.asyncio
async def test_changing_timezone_overwrites_the_previous_value(api_client, tmp_db_path: str) -> None:
    client, user_id, _admin_id = api_client
    client.get(_BOOT, headers={"X-Lingo-Timezone": "America/Denver"})
    client.get(_BOOT, headers={"X-Lingo-Timezone": "Asia/Tokyo"})

    record = await _read_user(tmp_db_path, user_id)
    assert record["timezone"] == "Asia/Tokyo"


@pytest.mark.asyncio
async def test_unchanged_timezone_does_not_rewrite_the_row(api_client, tmp_db_path: str) -> None:
    """Second request with the SAME zone must not touch `updated_at` — the
    conditional write in `sync_request_timezone` is skipped when the
    header already matches what's stored (numbers-as-required: one write
    per actual timezone change, not one per request)."""
    client, user_id, _admin_id = api_client
    client.get(_BOOT, headers={"X-Lingo-Timezone": "America/Denver"})
    first = await _read_user(tmp_db_path, user_id)

    client.get(_BOOT, headers={"X-Lingo-Timezone": "America/Denver"})
    second = await _read_user(tmp_db_path, user_id)

    assert second["timezone"] == "America/Denver"
    assert second["updated_at"] == first["updated_at"]


@pytest.mark.asyncio
async def test_admin_impersonating_syncs_to_the_admins_own_row_not_the_targets(api_client, tmp_db_path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The header describes the ADMIN's physical device, not the
    impersonation target's — syncing it onto the target's row would be
    wrong (and would let an impersonated session silently overwrite a
    real user's stored zone with the admin's own)."""
    client, user_id, admin_id = api_client

    from app.config import settings as cfg

    monkeypatch.setattr(cfg, "ADMIN_USER_IDS", [admin_id])

    resp = client.get(
        _BOOT,
        headers={
            "X-Dev-User": "dev|admin-user",
            "X-Lingo-Timezone": "Asia/Tokyo",
            "X-Impersonate-User-Id": user_id,
        },
    )
    assert resp.status_code == 200

    admin_record = await _read_user(tmp_db_path, admin_id)
    target_record = await _read_user(tmp_db_path, user_id)
    assert admin_record["timezone"] == "Asia/Tokyo"
    assert target_record.get("timezone") != "Asia/Tokyo"
