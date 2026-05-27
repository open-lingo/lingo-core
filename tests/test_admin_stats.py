"""Tests for admin home-dashboard endpoints (user stats + list filters)."""

from __future__ import annotations


def _make_admin(monkeypatch, admin_user_id: str) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "ADMIN_USER_IDS", [admin_user_id])


def test_user_stats_endpoint(api_client, monkeypatch) -> None:
    """GET /admin/stats/users returns total/new/active counts."""
    client, _user_id, admin_user_id = api_client
    _make_admin(monkeypatch, admin_user_id)

    resp = client.get(
        "/api/core/v1/admin/stats/users",
        headers={"X-Dev-User": "dev|admin-user"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["since_days"] == 7
    # Two users were registered in conftest, both "new" within the window.
    assert body["total"] >= 2
    assert body["new_since"] >= 2
    assert "active_since" in body


def test_user_stats_non_admin_blocked(api_client) -> None:
    """Non-admin user must get 403."""
    client, _user_id, _admin_user_id = api_client
    resp = client.get("/api/core/v1/admin/stats/users")
    assert resp.status_code == 403, resp.text


def test_list_users_search_filter(api_client, monkeypatch) -> None:
    """list_users supports a substring search on username/display_name."""
    client, _user_id, admin_user_id = api_client
    _make_admin(monkeypatch, admin_user_id)

    resp = client.get(
        "/api/core/v1/admin/users",
        params={"search": "adm"},
        headers={"X-Dev-User": "dev|admin-user"},
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    # All returned rows contain "adm" in username (conftest registers
    # admin user as "adm<6 hex>").
    for u in items:
        assert "adm" in u["username"].lower() or "adm" in (u.get("display_name") or "").lower()
    assert items, "expected at least the admin user back"


def test_list_users_invalid_sort_rejected(api_client, monkeypatch) -> None:
    """The sort param is whitelisted at the FastAPI layer."""
    client, _user_id, admin_user_id = api_client
    _make_admin(monkeypatch, admin_user_id)

    resp = client.get(
        "/api/core/v1/admin/users",
        params={"sort": "; DROP TABLE users; --"},
        headers={"X-Dev-User": "dev|admin-user"},
    )
    assert resp.status_code == 422, resp.text


def test_list_users_sort_by_xp_desc(api_client, monkeypatch, sqlite_user_repo) -> None:
    """Sort=xp/order=desc returns the highest-xp users first."""
    import asyncio

    # Bump XP on one of the conftest users directly via the repo to make
    # the ordering deterministic.
    client, user_id, admin_user_id = api_client
    _make_admin(monkeypatch, admin_user_id)

    # The repo from the fixture is a fresh in-memory copy; mutate the
    # production repo (the one the app uses) by hitting the award-xp
    # endpoint, which writes to the same database the test client uses.
    resp = client.post(
        f"/api/core/v1/admin/users/{admin_user_id}/award-xp",
        json={"amount": 250, "reason": "stats test"},
        headers={"X-Dev-User": "dev|admin-user"},
    )
    assert resp.status_code == 200, resp.text

    resp = client.get(
        "/api/core/v1/admin/users",
        params={"sort": "xp", "order": "desc"},
        headers={"X-Dev-User": "dev|admin-user"},
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert items[0]["id"] == admin_user_id
    # Defensive: silence pytest about unused fixtures.
    _ = (user_id, sqlite_user_repo, asyncio)
