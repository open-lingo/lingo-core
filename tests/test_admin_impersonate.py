"""Admin impersonation — start/stop endpoints + X-Impersonate-User-Id header.

Covers:
  - happy path: admin starts → /users/me reflects the target's record.
  - 403 / silent-ignore: non-admin sending the header gets their own user.
  - 404: admin sets the header with a bogus target_id.
  - audit log: impersonate_start / impersonate_request / impersonate_stop
    appear in the audit log with the admin as actor.
"""


def _admin_headers(target: str | None = None) -> dict[str, str]:
    headers = {"X-Dev-User": "dev|admin-user"}
    if target:
        headers["X-Impersonate-User-Id"] = target
    return headers


def test_impersonate_start_returns_target_fields(api_client, monkeypatch) -> None:
    client, user_id, admin_user_id = api_client

    from app.config import settings as cfg

    monkeypatch.setattr(cfg, "ADMIN_USER_IDS", [admin_user_id])

    resp = client.post(
        f"/api/core/v1/admin/impersonate/{user_id}/start",
        headers=_admin_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["target_user_id"] == user_id
    assert body["target_username"]
    # display_name comes back even if empty string — never null.
    assert "target_display_name" in body


def test_impersonate_start_unknown_user_404(api_client, monkeypatch) -> None:
    client, _user_id, admin_user_id = api_client

    from app.config import settings as cfg

    monkeypatch.setattr(cfg, "ADMIN_USER_IDS", [admin_user_id])

    resp = client.post(
        "/api/core/v1/admin/impersonate/00000000-0000-0000-0000-000000000000/start",
        headers=_admin_headers(),
    )
    assert resp.status_code == 404, resp.text


def test_impersonate_start_non_admin_blocked(api_client) -> None:
    client, user_id, _admin_user_id = api_client

    # Default dev user is not in the allow-list.
    resp = client.post(f"/api/core/v1/admin/impersonate/{user_id}/start")
    assert resp.status_code == 403, resp.text


def test_impersonate_stop_204(api_client, monkeypatch) -> None:
    client, _user_id, admin_user_id = api_client

    from app.config import settings as cfg

    monkeypatch.setattr(cfg, "ADMIN_USER_IDS", [admin_user_id])

    resp = client.post(
        "/api/core/v1/admin/impersonate/stop",
        headers=_admin_headers(),
    )
    assert resp.status_code == 204, resp.text


def test_impersonate_header_returns_target_me(api_client, monkeypatch) -> None:
    """The headline: GET /users/me with X-Impersonate-User-Id returns the
    target user's record (not the admin's). This proves get_acting_user
    actually swapped the identity."""
    client, user_id, admin_user_id = api_client

    from app.config import settings as cfg

    monkeypatch.setattr(cfg, "ADMIN_USER_IDS", [admin_user_id])

    resp = client.get(
        "/api/core/v1/users/me",
        headers=_admin_headers(target=user_id),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == user_id, "expected target user, got admin"


def test_impersonate_header_ignored_for_non_admin(api_client) -> None:
    """If a regular user sends the header (e.g. their token leaked into
    a misconfigured client) it must be silently ignored — their own
    /users/me must still resolve to themselves, not 403."""
    client, user_id, admin_user_id = api_client

    # Non-admin caller (default dev user) tries to impersonate the admin.
    resp = client.get(
        "/api/core/v1/users/me",
        headers={"X-Impersonate-User-Id": admin_user_id},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == user_id, "non-admin must not be able to impersonate"


def test_impersonate_unknown_target_404(api_client, monkeypatch) -> None:
    """Admin sets the header with a bogus uuid — 404, not silent fallback,
    so the admin notices the typo."""
    client, _user_id, admin_user_id = api_client

    from app.config import settings as cfg

    monkeypatch.setattr(cfg, "ADMIN_USER_IDS", [admin_user_id])

    resp = client.get(
        "/api/core/v1/users/me",
        headers=_admin_headers(target="00000000-0000-0000-0000-000000000000"),
    )
    assert resp.status_code == 404, resp.text


def test_impersonate_writes_audit_entries(api_client, monkeypatch) -> None:
    """Start + one impersonated request + stop should produce three audit
    entries with the admin's id as actor and the target as target_id."""
    client, user_id, admin_user_id = api_client

    from app.config import settings as cfg

    monkeypatch.setattr(cfg, "ADMIN_USER_IDS", [admin_user_id])

    # 1. start
    r1 = client.post(
        f"/api/core/v1/admin/impersonate/{user_id}/start",
        headers=_admin_headers(),
    )
    assert r1.status_code == 200, r1.text

    # 2. one impersonated request
    r2 = client.get(
        "/api/core/v1/users/me",
        headers=_admin_headers(target=user_id),
    )
    assert r2.status_code == 200, r2.text

    # 3. stop
    r3 = client.post(
        "/api/core/v1/admin/impersonate/stop",
        headers=_admin_headers(),
    )
    assert r3.status_code == 204, r3.text

    # Pull the audit log, filter to this admin actor.
    audit = client.get(
        "/api/core/v1/admin/audit",
        params={"actor_id": admin_user_id, "limit": 100},
        headers=_admin_headers(),
    )
    assert audit.status_code == 200, audit.text
    items = audit.json()["items"]
    actions = [item["action"] for item in items]
    # Each action appears at least once (most-recent first; stop is
    # newest, request, start are older).
    assert "impersonate_start" in actions
    assert "impersonate_request" in actions
    assert "impersonate_stop" in actions
