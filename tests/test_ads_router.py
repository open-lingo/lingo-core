"""Tests for POST /ads/watched — rewarded-ad lingot credit (v1).

Covers:
  - Happy path: credit lands, response shape matches the FE contract.
  - Dedup: a second hit with the same idempotency_key returns 429.
  - Different keys for the same user both credit.
  - Auth: a missing JWT (no dev-user header, DEBUG off) returns 401.
"""

import pytest


def _auth_headers() -> dict[str, str]:
    return {"X-Dev-User": "dev|test-user"}


@pytest.fixture(autouse=True)
def _reset_dedup() -> None:
    """Each test gets a clean in-process dedup set."""
    from app.ads.router import _reset_dedup_for_tests

    _reset_dedup_for_tests()
    yield
    _reset_dedup_for_tests()


def test_watched_ad_credits_lingots(api_client) -> None:
    client, _user_id, _admin_user_id = api_client

    # Baseline — user starts with 0 lingots.
    me = client.get("/api/core/v1/users/me", headers=_auth_headers()).json()
    assert int(me.get("lingots") or 0) == 0

    resp = client.post(
        "/api/core/v1/ads/watched",
        json={"idempotency_key": "k-001", "placement": "modal_rewarded_v1"},
        headers=_auth_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"lingots_awarded": 5, "new_balance": 5}

    # Persisted on the user row.
    me2 = client.get("/api/core/v1/users/me", headers=_auth_headers()).json()
    assert int(me2["lingots"]) == 5


def test_watched_ad_dedup_returns_429(api_client) -> None:
    client, _user_id, _admin_user_id = api_client

    first = client.post(
        "/api/core/v1/ads/watched",
        json={"idempotency_key": "k-dup", "placement": "modal_rewarded_v1"},
        headers=_auth_headers(),
    )
    assert first.status_code == 200, first.text
    assert first.json()["new_balance"] == 5

    second = client.post(
        "/api/core/v1/ads/watched",
        json={"idempotency_key": "k-dup", "placement": "modal_rewarded_v1"},
        headers=_auth_headers(),
    )
    assert second.status_code == 429, second.text
    assert second.json()["detail"] == "already_credited"

    # Balance is unchanged after the dedup hit — credit only ran once.
    me = client.get("/api/core/v1/users/me", headers=_auth_headers()).json()
    assert int(me["lingots"]) == 5


def test_watched_ad_distinct_keys_both_credit(api_client) -> None:
    client, _user_id, _admin_user_id = api_client

    for key, expected in (("k-a", 5), ("k-b", 10), ("k-c", 15)):
        resp = client.post(
            "/api/core/v1/ads/watched",
            json={"idempotency_key": key, "placement": "modal_rewarded_v1"},
            headers=_auth_headers(),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["new_balance"] == expected


def test_watched_ad_requires_auth(api_client, monkeypatch) -> None:
    """With DEBUG off and no JWT, the route should 401."""
    client, _user_id, _admin_user_id = api_client

    # The conftest leaves DEBUG=true (dev-user bypass). Flip it off via the
    # live settings object so get_current_user falls through to JWT auth and
    # finds no credentials.
    from app.config import settings as live_settings

    monkeypatch.setattr(live_settings, "DEBUG", False)

    resp = client.post(
        "/api/core/v1/ads/watched",
        json={"idempotency_key": "k-noauth", "placement": "modal_rewarded_v1"},
    )
    assert resp.status_code == 401, resp.text
