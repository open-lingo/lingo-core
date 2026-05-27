"""Admin moderation of friend requests — happy paths for the new
``/admin/social/users/{user_id}/friend-requests`` endpoints.

Drives the API through the conftest ``api_client`` fixture (DEBUG=true +
X-Dev-User) and uses a third user as the "other" party so the admin can
act on the requests on behalf of one of them.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest


def _register(client: Any, sub: str, username: str, display_name: str) -> dict[str, Any]:
    resp = client.post(
        "/api/core/v1/users/me",
        json={"username": username, "display_name": display_name},
        headers={"X-Dev-User": sub},
    )
    assert resp.status_code in (200, 201, 409), resp.text
    if resp.status_code == 409:
        resp = client.get("/api/core/v1/users/me", headers={"X-Dev-User": sub})
        assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture()
def admin_client(api_client, monkeypatch):
    """Wrap the standard api_client fixture but flip the admin allowlist on."""
    client, user_id, admin_user_id = api_client
    from app.config import settings

    monkeypatch.setattr(settings, "ADMIN_USER_IDS", [admin_user_id])
    return client, user_id, admin_user_id


def _as(sub: str) -> dict[str, str]:
    return {"X-Dev-User": sub}


def test_admin_list_friend_requests(admin_client) -> None:
    client, _user_id, _admin_user_id = admin_client
    # Two ordinary users — Alice sends Bob a friend request.
    alice = _register(client, "dev|alice", f"alice{uuid.uuid4().hex[:6]}", "Alice")
    bob = _register(client, "dev|bob", f"bob{uuid.uuid4().hex[:6]}", "Bob")
    resp = client.post(
        "/api/core/v1/social/friends/requests",
        json={"to_user_id": bob["id"]},
        headers=_as("dev|alice"),
    )
    assert resp.status_code == 200, resp.text

    # Admin lists Bob's friend-request inbox.
    resp = client.get(
        f"/api/core/v1/admin/social/users/{bob['id']}/friend-requests",
        headers=_as("dev|admin-user"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert any(r["user_id"] == alice["id"] for r in body["incoming"])

    # And Alice's outbox.
    resp = client.get(
        f"/api/core/v1/admin/social/users/{alice['id']}/friend-requests",
        headers=_as("dev|admin-user"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert any(r["user_id"] == bob["id"] for r in body["outgoing"])


def test_admin_accept_friend_request(admin_client) -> None:
    client, _user_id, _admin_user_id = admin_client
    alice = _register(client, "dev|alice", f"alice{uuid.uuid4().hex[:6]}", "Alice")
    bob = _register(client, "dev|bob", f"bob{uuid.uuid4().hex[:6]}", "Bob")
    resp = client.post(
        "/api/core/v1/social/friends/requests",
        json={"to_user_id": bob["id"]},
        headers=_as("dev|alice"),
    )
    assert resp.status_code == 200, resp.text

    # Admin accepts on Bob's behalf.
    resp = client.post(
        f"/api/core/v1/admin/social/users/{bob['id']}/friend-requests/{alice['id']}/accept",
        headers=_as("dev|admin-user"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "accepted"

    # Friends list reflects the accepted edge from both sides.
    resp = client.get("/api/core/v1/social/friends", headers=_as("dev|alice"))
    assert any(f["user_id"] == bob["id"] for f in resp.json())
    resp = client.get("/api/core/v1/social/friends", headers=_as("dev|bob"))
    assert any(f["user_id"] == alice["id"] for f in resp.json())


def test_admin_decline_friend_request(admin_client) -> None:
    client, _user_id, _admin_user_id = admin_client
    alice = _register(client, "dev|alice", f"alice{uuid.uuid4().hex[:6]}", "Alice")
    bob = _register(client, "dev|bob", f"bob{uuid.uuid4().hex[:6]}", "Bob")
    resp = client.post(
        "/api/core/v1/social/friends/requests",
        json={"to_user_id": bob["id"]},
        headers=_as("dev|alice"),
    )
    assert resp.status_code == 200, resp.text

    # Admin declines on Bob's behalf.
    resp = client.delete(
        f"/api/core/v1/admin/social/users/{bob['id']}/friend-requests/{alice['id']}",
        headers=_as("dev|admin-user"),
    )
    assert resp.status_code == 204, resp.text

    # No pending request remains.
    resp = client.get(
        f"/api/core/v1/admin/social/users/{bob['id']}/friend-requests",
        headers=_as("dev|admin-user"),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert not any(r["user_id"] == alice["id"] for r in body["incoming"])
    # And Alice doesn't list it as outgoing.
    resp = client.get(
        f"/api/core/v1/admin/social/users/{alice['id']}/friend-requests",
        headers=_as("dev|admin-user"),
    )
    body = resp.json()
    assert not any(r["user_id"] == bob["id"] for r in body["outgoing"])


def test_admin_endpoints_block_non_admin(api_client) -> None:
    """Sanity: the admin-social router is gated by require_admin."""
    client, _user_id, _admin_user_id = api_client
    # User in path doesn't have to exist for the gating check to fire first.
    resp = client.get(
        "/api/core/v1/admin/social/users/some-uuid/friend-requests",
    )
    assert resp.status_code == 403, resp.text
