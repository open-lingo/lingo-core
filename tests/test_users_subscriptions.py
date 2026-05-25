"""Subscriptions API tests — content_type validation (Fix 10)."""


def test_list_subscriptions_rejects_unknown_content_type(api_client) -> None:
    """A bogus ``content_type`` query param must return 400, not an empty list."""
    client, _user_id, _ = api_client

    resp = client.get(
        "/api/core/v1/users/me/subscriptions?content_type=not-a-real-type"
    )
    assert resp.status_code == 400, resp.text


def test_list_subscriptions_accepts_valid_content_type(api_client) -> None:
    """A known ContentType must be accepted (returns empty list for empty user)."""
    client, _user_id, _ = api_client

    resp = client.get("/api/core/v1/users/me/subscriptions?content_type=deck")
    assert resp.status_code == 200, resp.text


def test_list_subscriptions_no_filter_works(api_client) -> None:
    """Omitting content_type must still work."""
    client, _user_id, _ = api_client

    resp = client.get("/api/core/v1/users/me/subscriptions")
    assert resp.status_code == 200, resp.text
