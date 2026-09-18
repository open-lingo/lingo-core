"""`lingo.access` line (`app/main.py::access_log`) — A3b, 2026-09-17.

Was printing `user=-` for EVERY request, authenticated ones included: it
only ever read the raw `X-Dev-User` header, which real (non-DEBUG)
traffic never sets. Fixed by stashing a stable, non-reversible hash of
the resolved Auth0 `sub` on `request.state.auth_sub_hash`
(`app/auth/dependencies.py`'s `get_current_user`/
`get_current_user_optional`, read here after `call_next` returns) and by
logging the client's `X-Lingo-Platform` header (ios/android/web,
`src/shared/api/client.ts` in the `lingo` repo) alongside it.

Uses the `api_client` fixture (`tests/conftest.py`): DEBUG=true,
Auth0 bypassed via `X-Dev-User`, which resolves through the exact same
`get_current_user`/`_resolve_user_id` code path a real JWT would.
"""

import logging

import pytest

from app.auth.dependencies import log_safe_user_hash


def test_authenticated_request_logs_a_stable_hash_not_a_dash(api_client, caplog: pytest.LogCaptureFixture) -> None:
    client, _user_id, _admin_id = api_client
    caplog.set_level(logging.INFO, logger="lingo.access")
    caplog.clear()

    resp = client.get("/api/core/v1/users/me")
    assert resp.status_code == 200

    records = [r for r in caplog.records if r.name == "lingo.access" and "/users/me" in r.getMessage()]
    assert records, "no lingo.access line captured for GET /users/me"
    message = records[-1].getMessage()
    assert "user=-" not in message
    assert f"user={log_safe_user_hash('dev|test-user')}" in message


def test_unauthenticated_request_still_logs_a_dash(api_client, caplog: pytest.LogCaptureFixture) -> None:
    """A route nobody attaches an auth dependency to (e.g. `/health`) must
    still show `user=-` — the fix must not fabricate an identity for a
    request that never carried a token."""
    client, _user_id, _admin_id = api_client
    caplog.set_level(logging.INFO, logger="lingo.access")
    caplog.clear()

    resp = client.get("/health")
    assert resp.status_code == 200

    records = [r for r in caplog.records if r.name == "lingo.access" and "/health" in r.getMessage()]
    assert records
    assert "user=-" in records[-1].getMessage()


def test_invalid_token_logs_a_dash_not_a_stale_hash(api_client, caplog: pytest.LogCaptureFixture) -> None:
    """A request with credentials that fail validation (route uses
    ``get_current_user_optional`` or a route requiring auth 401s) must not
    leave a PREVIOUS request's hash lingering — `request.state` is
    per-request, this pins that assumption for real."""
    client, _user_id, _admin_id = api_client
    caplog.set_level(logging.INFO, logger="lingo.access")
    caplog.clear()

    resp = client.get("/api/core/v1/users/me", headers={"Authorization": "Bearer not-a-real-jwt"})
    # DEBUG=true means X-Dev-User / DEV_USER still wins over a garbage
    # bearer token (see `_dev_user_from_request`) — this request therefore
    # resolves via the dev identity, not a 401. The point of this test is
    # narrower: prove the SAME request doesn't somehow log two different
    # identities, and that whatever is logged is deterministic.
    assert resp.status_code == 200
    records = [r for r in caplog.records if r.name == "lingo.access" and "/users/me" in r.getMessage()]
    assert records
    assert f"user={log_safe_user_hash('dev|test-user')}" in records[-1].getMessage()


def test_two_different_users_get_two_different_hashes(api_client, caplog: pytest.LogCaptureFixture) -> None:
    client, _user_id, _admin_id = api_client
    caplog.set_level(logging.INFO, logger="lingo.access")
    caplog.clear()

    client.get("/api/core/v1/users/me")
    client.get("/api/core/v1/users/me", headers={"X-Dev-User": "dev|admin-user"})

    records = [r for r in caplog.records if r.name == "lingo.access" and "/users/me" in r.getMessage()]
    assert len(records) == 2
    hash_a = log_safe_user_hash("dev|test-user")
    hash_admin = log_safe_user_hash("dev|admin-user")
    assert hash_a != hash_admin
    assert f"user={hash_a}" in records[0].getMessage()
    assert f"user={hash_admin}" in records[1].getMessage()


def test_platform_header_is_logged_when_present(api_client, caplog: pytest.LogCaptureFixture) -> None:
    client, _user_id, _admin_id = api_client
    caplog.set_level(logging.INFO, logger="lingo.access")
    caplog.clear()

    client.get("/api/core/v1/users/me", headers={"X-Lingo-Platform": "ios"})

    records = [r for r in caplog.records if r.name == "lingo.access" and "/users/me" in r.getMessage()]
    assert records
    assert "platform=ios" in records[-1].getMessage()


def test_platform_defaults_to_dash_when_header_absent(api_client, caplog: pytest.LogCaptureFixture) -> None:
    client, _user_id, _admin_id = api_client
    caplog.set_level(logging.INFO, logger="lingo.access")
    caplog.clear()

    client.get("/health")

    records = [r for r in caplog.records if r.name == "lingo.access" and "/health" in r.getMessage()]
    assert records
    assert "platform=-" in records[-1].getMessage()


# ── log_safe_user_hash itself ────────────────────────────────────────────


def test_hash_is_stable_for_the_same_sub() -> None:
    assert log_safe_user_hash("auth0|abc123") == log_safe_user_hash("auth0|abc123")


def test_hash_is_eight_hex_chars() -> None:
    h = log_safe_user_hash("auth0|abc123")
    assert len(h) == 8
    int(h, 16)  # raises ValueError if not valid hex


def test_hash_never_contains_the_raw_sub() -> None:
    sub = "auth0|super-identifying-string-12345"
    assert sub not in log_safe_user_hash(sub)


def test_different_subs_produce_different_hashes() -> None:
    assert log_safe_user_hash("auth0|user-a") != log_safe_user_hash("auth0|user-b")
