"""Sad-path tests for the Phase 3 ``api_error`` refactor.

Verify that when a repo method raises an unexpected exception, the routers
in ``users``, ``admin``, and ``community`` surface a clean 500 with the
context label produced by ``api_error("...")``, rather than leaking a stack
trace or a generic 500.
"""

from unittest.mock import AsyncMock

import pytest

# ── users router ──────────────────────────────────────────────────────────


def test_users_patch_settings_repo_failure_returns_500_with_context(api_client, monkeypatch: pytest.MonkeyPatch) -> None:
    """``PATCH /users/me/settings`` wraps the repo call in
    ``api_error("updating user settings")``; a repo blow-up must surface as
    500 with that label.

    We mock ``update_settings`` rather than ``get_user_by_id`` because the
    latter is also called by ``get_registered_user`` during auth resolution,
    which would short-circuit the test before reaching the router.
    """
    client, _user_id, _admin_user_id = api_client

    from app.db import provider as provider_mod

    repo = provider_mod.get_user_repo()
    monkeypatch.setattr(
        repo,
        "update_settings",
        AsyncMock(side_effect=RuntimeError("boom: dynamo down")),
    )

    resp = client.patch(
        "/api/core/v1/users/me/settings",
        json={"theme": "dark"},
    )
    assert resp.status_code == 500, resp.text
    assert resp.json()["detail"] == "Error updating user settings"


# ── admin router ──────────────────────────────────────────────────────────


def test_admin_list_users_repo_failure_returns_500_with_context(api_client, monkeypatch: pytest.MonkeyPatch) -> None:
    """``GET /admin/users`` wraps ``list_users`` in ``api_error("listing
    users")``; a repo blow-up must surface as 500 with that label."""
    client, _user_id, admin_user_id = api_client

    from app.config import settings
    from app.db import provider as provider_mod

    monkeypatch.setattr(settings, "ADMIN_USER_IDS", [admin_user_id])

    repo = provider_mod.get_user_repo()
    monkeypatch.setattr(
        repo,
        "list_users",
        AsyncMock(side_effect=RuntimeError("boom: dynamo down")),
    )

    resp = client.get(
        "/api/core/v1/admin/users",
        headers={"X-Dev-User": "dev|admin-user"},
    )
    assert resp.status_code == 500, resp.text
    assert resp.json()["detail"] == "Error listing users"


# ── community router ─────────────────────────────────────────────────────


def test_community_list_categories_repo_failure_returns_500_with_context(api_client, monkeypatch: pytest.MonkeyPatch) -> None:
    """``GET /community/categories`` wraps the repo call in
    ``api_error("listing categories")``; a repo blow-up must surface as 500
    with that label."""
    client, _user_id, _admin_user_id = api_client

    from app.db import provider as provider_mod

    repo = provider_mod.get_community_repo()
    monkeypatch.setattr(
        repo,
        "list_categories",
        AsyncMock(side_effect=RuntimeError("boom: backing store down")),
    )

    resp = client.get("/api/core/v1/community/categories")
    assert resp.status_code == 500, resp.text
    assert resp.json()["detail"] == "Error listing categories"
