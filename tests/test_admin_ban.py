"""Tests for /admin/users/{id}/ban + /unban — moderation history shape."""

from __future__ import annotations


def _make_admin(monkeypatch, admin_user_id: str) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "ADMIN_USER_IDS", [admin_user_id])


def test_ban_user_writes_history_and_status(api_client, monkeypatch) -> None:
    client, user_id, admin_user_id = api_client
    _make_admin(monkeypatch, admin_user_id)

    resp = client.post(
        f"/api/core/v1/admin/users/{user_id}/ban",
        json={
            "type": "account",
            "reason": "Spam",
            "duration": "24h",
            "notes": "first offense",
        },
        headers={"X-Dev-User": "dev|admin-user"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "banned"
    assert body["status_expiration"] is not None
    assert len(body["account_ban_history"]) == 1
    record = body["account_ban_history"][0]
    assert record["reason"] == "Spam"
    assert record["moderator_id"] == admin_user_id
    assert record["ended_at"] is None
    assert record["notes"] == "first offense"


def test_ban_history_capped_at_two(api_client, monkeypatch) -> None:
    """Per the design memo: max 2 entries per ban type, oldest dropped."""
    client, user_id, admin_user_id = api_client
    _make_admin(monkeypatch, admin_user_id)

    headers = {"X-Dev-User": "dev|admin-user"}
    for reason in ("first", "second", "third"):
        resp = client.post(
            f"/api/core/v1/admin/users/{user_id}/ban",
            json={"type": "community", "reason": reason, "duration": "7d"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text

    resp = client.get(
        f"/api/core/v1/admin/users/{user_id}",
        headers=headers,
    )
    history = resp.json()["community_ban_history"]
    assert len(history) == 2
    assert [r["reason"] for r in history] == ["second", "third"]


def test_ban_permanent_has_no_expiry(api_client, monkeypatch) -> None:
    client, user_id, admin_user_id = api_client
    _make_admin(monkeypatch, admin_user_id)

    resp = client.post(
        f"/api/core/v1/admin/users/{user_id}/ban",
        json={"type": "account", "reason": "TOS", "duration": "permanent"},
        headers={"X-Dev-User": "dev|admin-user"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status_expiration"] is None
    assert body["account_ban_history"][0]["expires_at"] is None


def test_unban_closes_most_recent_open_record(api_client, monkeypatch) -> None:
    client, user_id, admin_user_id = api_client
    _make_admin(monkeypatch, admin_user_id)
    headers = {"X-Dev-User": "dev|admin-user"}

    client.post(
        f"/api/core/v1/admin/users/{user_id}/ban",
        json={"type": "account", "reason": "first", "duration": "7d"},
        headers=headers,
    )
    resp = client.post(
        f"/api/core/v1/admin/users/{user_id}/unban",
        json={"type": "account"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "active"
    assert body["status_expiration"] is None
    record = body["account_ban_history"][0]
    assert record["ended_at"] is not None


def test_admin_cannot_ban_self(api_client, monkeypatch) -> None:
    client, _user_id, admin_user_id = api_client
    _make_admin(monkeypatch, admin_user_id)

    resp = client.post(
        f"/api/core/v1/admin/users/{admin_user_id}/ban",
        json={"type": "account", "reason": "test", "duration": "24h"},
        headers={"X-Dev-User": "dev|admin-user"},
    )
    assert resp.status_code == 403, resp.text


def test_ban_endpoint_admin_only(api_client) -> None:
    client, user_id, _admin_user_id = api_client
    resp = client.post(
        f"/api/core/v1/admin/users/{user_id}/ban",
        json={"type": "account", "reason": "x", "duration": "24h"},
    )
    assert resp.status_code == 403, resp.text


def test_list_users_filter_by_community_status(api_client, monkeypatch) -> None:
    """list_users supports community_status filter for the banned-users tab."""
    client, user_id, admin_user_id = api_client
    _make_admin(monkeypatch, admin_user_id)
    headers = {"X-Dev-User": "dev|admin-user"}

    client.post(
        f"/api/core/v1/admin/users/{user_id}/ban",
        json={"type": "community", "reason": "trolling", "duration": "7d"},
        headers=headers,
    )

    resp = client.get(
        "/api/core/v1/admin/users",
        params={"community_status": "banned"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == user_id
    assert items[0]["community_status"] == "banned"
