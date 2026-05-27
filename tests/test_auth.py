"""Auth tests — JWKS cache TTL refresh (Fix 7) and user-id cache (Fix 8)."""

import pytest


@pytest.mark.asyncio
async def test_jwks_cache_invalidates_after_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """After the TTL expires, _get_jwks must re-fetch from Auth0."""
    from app.auth import dependencies as auth_dep

    # Reset the cache.
    monkeypatch.setattr(auth_dep, "_jwks_cache", None)
    monkeypatch.setattr(auth_dep, "_jwks_cache_at", 0.0)

    fake_jwks = {"keys": [{"kty": "RSA", "kid": "k1", "n": "x", "e": "AQAB"}]}
    call_count = {"n": 0}

    class _FakeResp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return fake_jwks

    class _FakeClient:
        async def __aenter__(self) -> "_FakeClient":
            return self

        async def __aexit__(self, *a: object) -> None:
            return None

        async def get(self, url: str) -> _FakeResp:
            call_count["n"] += 1
            return _FakeResp()

    monkeypatch.setattr(auth_dep.httpx, "AsyncClient", lambda *a, **kw: _FakeClient())

    # First fetch — populates cache.
    await auth_dep._get_jwks()
    assert call_count["n"] == 1
    # Second fetch — within TTL, uses cache.
    await auth_dep._get_jwks()
    assert call_count["n"] == 1
    # Force TTL expiry.
    monkeypatch.setattr(auth_dep, "_jwks_cache_at", 0.0)
    await auth_dep._get_jwks()
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_jwks_refresh_on_kid_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    """When a kid that's not in the cache shows up, refresh once."""
    from app.auth import dependencies as auth_dep

    # Pre-load cache with key 'k1' only.
    monkeypatch.setattr(
        auth_dep,
        "_jwks_cache",
        {"keys": [{"kty": "RSA", "kid": "k1", "n": "x", "e": "AQAB"}]},
    )
    import time as _t

    monkeypatch.setattr(auth_dep, "_jwks_cache_at", _t.time())
    monkeypatch.setattr(auth_dep, "_jwks_last_refresh", 0.0)

    refresh_jwks = {
        "keys": [
            {"kty": "RSA", "kid": "k1", "n": "x", "e": "AQAB"},
            {"kty": "RSA", "kid": "k2", "n": "y", "e": "AQAB"},
        ]
    }

    class _FakeResp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return refresh_jwks

    class _FakeClient:
        async def __aenter__(self) -> "_FakeClient":
            return self

        async def __aexit__(self, *a: object) -> None:
            return None

        async def get(self, url: str) -> _FakeResp:
            return _FakeResp()

    monkeypatch.setattr(auth_dep.httpx, "AsyncClient", lambda *a, **kw: _FakeClient())

    # Ask for kid 'k2' — not in cache → triggers refresh.
    keys = await auth_dep._get_rsa_keys_with_refresh("k2")
    kids = [k.get("kid") for k in keys]
    assert "k2" in kids


def test_user_id_cache_hits(api_client, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fix 8 — repeated authed requests reuse the cached auth0_sub→user_id
    lookup. The second call must NOT hit the user repo's get_user_by_auth0_id."""
    client, _user_id, _ = api_client

    # First call primes the cache (test conftest already did some requests).
    resp = client.get("/api/core/v1/users/me")
    assert resp.status_code == 200

    from app.db import provider

    repo = provider.get_user_repo()
    call_count = {"n": 0}
    real_lookup = repo.get_user_by_auth0_id

    async def counting_lookup(auth0_id):
        call_count["n"] += 1
        return await real_lookup(auth0_id)

    monkeypatch.setattr(repo, "get_user_by_auth0_id", counting_lookup)

    # Should hit cache — no repo call.
    resp = client.get("/api/core/v1/users/me")
    assert resp.status_code == 200
    assert call_count["n"] == 0, f"expected 0 lookups (cache hit), got {call_count['n']}"
