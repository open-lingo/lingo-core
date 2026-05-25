"""Decks API tests — batch endpoint cap (Fix 9) and per-deck voting."""


def test_batch_endpoint_caps_id_count(api_client) -> None:
    """More than 50 IDs in /decks/batch must return 400."""
    client, _user_id, _ = api_client

    ids = ",".join(f"d{i}" for i in range(51))
    resp = client.get(f"/api/core/v1/decks/batch?ids={ids}")
    assert resp.status_code == 400, resp.text
    assert "50" in resp.text or "too many" in resp.text.lower()


def test_batch_endpoint_accepts_50(api_client) -> None:
    """Exactly 50 IDs is the cap and must be accepted (returns empty list)."""
    client, _user_id, _ = api_client

    ids = ",".join(f"d{i}" for i in range(50))
    resp = client.get(f"/api/core/v1/decks/batch?ids={ids}")
    assert resp.status_code == 200, resp.text


# ── Voting ───────────────────────────────────────────────────────────────


def _create_deck(client) -> str:
    """Create a published deck owned by the test user. Returns deck_id."""
    resp = client.post(
        "/api/core/v1/decks",
        json={
            "languageId": "ja",
            "name": "Test Deck",
            "description": "for vote tests",
            "status": "published",
            "cards": [],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_vote_upvote_then_state(api_client) -> None:
    """POST /vote then GET /vote → count=1, voted=true."""
    client, _user_id, _ = api_client
    deck_id = _create_deck(client)

    resp = client.post(f"/api/core/v1/decks/{deck_id}/vote")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"count": 1, "voted": True}

    resp = client.get(f"/api/core/v1/decks/{deck_id}/vote")
    assert resp.status_code == 200
    assert resp.json() == {"count": 1, "voted": True}

    # voteCount surfaces on the GET /decks/{id} response too.
    resp = client.get(f"/api/core/v1/decks/{deck_id}")
    assert resp.status_code == 200
    assert resp.json()["voteCount"] == 1


def test_vote_idempotent(api_client) -> None:
    """Voting twice still shows count=1 — INSERT OR IGNORE collapses."""
    client, _user_id, _ = api_client
    deck_id = _create_deck(client)

    client.post(f"/api/core/v1/decks/{deck_id}/vote")
    resp = client.post(f"/api/core/v1/decks/{deck_id}/vote")
    assert resp.status_code == 200
    assert resp.json() == {"count": 1, "voted": True}


def test_vote_remove(api_client) -> None:
    """Vote, then unvote → count=0 voted=false."""
    client, _user_id, _ = api_client
    deck_id = _create_deck(client)

    client.post(f"/api/core/v1/decks/{deck_id}/vote")

    resp = client.delete(f"/api/core/v1/decks/{deck_id}/vote")
    assert resp.status_code == 200
    assert resp.json() == {"count": 0, "voted": False}

    resp = client.get(f"/api/core/v1/decks/{deck_id}/vote")
    assert resp.status_code == 200
    assert resp.json() == {"count": 0, "voted": False}


def test_vote_unknown_deck_returns_404(api_client) -> None:
    client, _user_id, _ = api_client
    resp = client.post("/api/core/v1/decks/does-not-exist/vote")
    assert resp.status_code == 404


def test_vote_state_other_user_voted_false(api_client) -> None:
    """A different user sees count but voted=false for the same deck."""
    client, _user_id, _ = api_client
    deck_id = _create_deck(client)
    client.post(f"/api/core/v1/decks/{deck_id}/vote")

    # Different user (the seeded admin identity) sees count=1, voted=false.
    resp = client.get(
        f"/api/core/v1/decks/{deck_id}/vote",
        headers={"X-Dev-User": "dev|admin-user"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 1
    assert body["voted"] is False
