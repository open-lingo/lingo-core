"""Admin RBAC tests (Fix 4) — non-admin must be blocked from admin routes;
seeded admin must pass."""


def test_non_admin_blocked(api_client) -> None:
    """A regular authenticated user must get 403 on admin routes."""
    client, _user_id, _admin_user_id = api_client

    resp = client.get("/api/core/v1/admin/users")
    assert resp.status_code == 403, resp.text


def test_admin_allowed_with_env_allowlist(api_client, monkeypatch) -> None:
    """A user whose internal id is in ADMIN_USER_IDS must pass admin gating."""
    client, _user_id, admin_user_id = api_client

    # Mutate settings.ADMIN_USER_IDS in place (the conftest already booted
    # the app; settings is a singleton). The dependency reads it per-call.
    from app.config import settings

    monkeypatch.setattr(settings, "ADMIN_USER_IDS", [admin_user_id])

    resp = client.get(
        "/api/core/v1/admin/users",
        headers={"X-Dev-User": "dev|admin-user"},
    )
    assert resp.status_code == 200, resp.text


def test_deck_admin_status_route_gated(api_client) -> None:
    """The admin deck approval route must be admin-only (Fix 4)."""
    client, _user_id, _admin_user_id = api_client

    resp = client.patch(
        "/api/core/v1/decks/admin/some-deck-id/status?status=published",
    )
    # Non-admin user must be blocked BEFORE the deck-existence check.
    assert resp.status_code == 403, resp.text
