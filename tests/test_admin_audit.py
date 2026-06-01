"""Tests for admin audit log (write hooks + read endpoint)."""

from __future__ import annotations


def _make_admin(monkeypatch, admin_user_id: str) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "ADMIN_USER_IDS", [admin_user_id])


def test_audit_endpoint_admin_only(api_client) -> None:
    client, _user_id, _admin_user_id = api_client
    resp = client.get("/api/core/v1/admin/audit")
    assert resp.status_code == 403, resp.text


def test_ban_records_audit_entry(api_client, monkeypatch) -> None:
    """Banning a user appends a row to the audit log."""
    client, user_id, admin_user_id = api_client
    _make_admin(monkeypatch, admin_user_id)
    headers = {"X-Dev-User": "dev|admin-user"}

    resp = client.post(
        f"/api/core/v1/admin/users/{user_id}/ban",
        json={"type": "account", "reason": "Spam", "duration": "24h"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    audit = client.get("/api/core/v1/admin/audit", headers=headers).json()
    items = audit["items"]
    assert items, "expected at least one audit entry"
    entry = items[0]
    assert entry["action"] == "ban_account"
    assert entry["target_id"] == user_id
    assert entry["target_kind"] == "user"
    assert entry["actor_id"] == admin_user_id
    assert entry["payload"]["reason"] == "Spam"
    assert entry["payload"]["duration"] == "24h"


def test_award_xp_records_audit_entry(api_client, monkeypatch) -> None:
    client, user_id, admin_user_id = api_client
    _make_admin(monkeypatch, admin_user_id)
    headers = {"X-Dev-User": "dev|admin-user"}

    resp = client.post(
        f"/api/core/v1/admin/users/{user_id}/award-xp",
        json={"amount": 50, "reason": "test grant"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    audit = client.get("/api/core/v1/admin/audit", headers=headers).json()
    actions = [it["action"] for it in audit["items"]]
    assert "award_xp" in actions


def test_audit_filter_by_target_kind(api_client, monkeypatch) -> None:
    client, user_id, admin_user_id = api_client
    _make_admin(monkeypatch, admin_user_id)
    headers = {"X-Dev-User": "dev|admin-user"}

    # Two writes of different kinds.
    client.post(
        f"/api/core/v1/admin/users/{user_id}/award-xp",
        json={"amount": 50, "reason": ""},
        headers=headers,
    )
    client.post(
        f"/api/core/v1/admin/users/{user_id}/ban",
        json={"type": "community", "reason": "x", "duration": "7d"},
        headers=headers,
    )

    audit = client.get(
        "/api/core/v1/admin/audit",
        params={"target_kind": "user"},
        headers=headers,
    ).json()
    assert all(item["target_kind"] == "user" for item in audit["items"])
    assert len(audit["items"]) >= 2
