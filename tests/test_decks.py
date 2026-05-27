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


# ── Tags on decks ────────────────────────────────────────────────────────


def _make_admin(monkeypatch, admin_user_id: str) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "ADMIN_USER_IDS", [admin_user_id])


def _seed_tag(client, slug: str, display: str = "") -> None:
    resp = client.post(
        "/api/core/v1/admin/tags",
        json={"slug": slug, "display_name": display or slug},
        headers={"X-Dev-User": "dev|admin-user"},
    )
    assert resp.status_code == 201, resp.text


def test_deck_with_tags_round_trips(api_client, monkeypatch) -> None:
    """Create a deck with tags → GET surfaces them; PUT can replace; GET reflects."""
    client, _user_id, admin_user_id = api_client
    _make_admin(monkeypatch, admin_user_id)

    _seed_tag(client, "jlpt-n5")
    _seed_tag(client, "vocabulary")
    _seed_tag(client, "kdrama")

    # Create with two tags.
    resp = client.post(
        "/api/core/v1/decks",
        json={
            "languageId": "ja",
            "name": "Tagged Deck",
            "status": "published",
            "cards": [],
            "tags": ["jlpt-n5", "vocabulary"],
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert sorted(body["tags"]) == ["jlpt-n5", "vocabulary"]
    deck_id = body["id"]

    # GET reflects the tags.
    resp = client.get(f"/api/core/v1/decks/{deck_id}")
    assert resp.status_code == 200
    assert sorted(resp.json()["tags"]) == ["jlpt-n5", "vocabulary"]

    # PUT replaces the tag set.
    resp = client.put(
        f"/api/core/v1/decks/{deck_id}",
        json={"tags": ["kdrama"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["tags"] == ["kdrama"]

    # PUT with no tags field leaves tags untouched.
    resp = client.put(
        f"/api/core/v1/decks/{deck_id}",
        json={"description": "no tag change"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["tags"] == ["kdrama"]

    # PUT with empty list clears tags.
    resp = client.put(
        f"/api/core/v1/decks/{deck_id}",
        json={"tags": []},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["tags"] == []


def test_deck_create_with_unknown_tag_404s(api_client, monkeypatch) -> None:
    client, _user_id, admin_user_id = api_client
    _make_admin(monkeypatch, admin_user_id)

    _seed_tag(client, "jlpt-n5")

    resp = client.post(
        "/api/core/v1/decks",
        json={
            "languageId": "ja",
            "name": "Bad Tag Deck",
            "status": "published",
            "cards": [],
            "tags": ["jlpt-n5", "made-up-slug"],
        },
    )
    assert resp.status_code == 404, resp.text
    detail = resp.json()["detail"]
    # detail is a dict {error, missing} — assert the missing slug shows up.
    assert "made-up-slug" in str(detail)


def test_deck_update_with_unknown_tag_404s(api_client, monkeypatch) -> None:
    client, _user_id, admin_user_id = api_client
    _make_admin(monkeypatch, admin_user_id)

    _seed_tag(client, "jlpt-n5")
    resp = client.post(
        "/api/core/v1/decks",
        json={"languageId": "ja", "name": "x", "status": "published", "cards": []},
    )
    deck_id = resp.json()["id"]

    resp = client.put(
        f"/api/core/v1/decks/{deck_id}",
        json={"tags": ["jlpt-n5", "not-a-real-tag"]},
    )
    assert resp.status_code == 404, resp.text


def test_admin_can_promote_and_demote_tag(api_client, monkeypatch) -> None:
    """Verifies the full admin tag lifecycle: create, attach to deck, remove."""
    client, _user_id, admin_user_id = api_client
    _make_admin(monkeypatch, admin_user_id)

    # Admin promotes a tag.
    _seed_tag(client, "business", "Business")

    # User attaches it to a deck.
    resp = client.post(
        "/api/core/v1/decks",
        json={
            "languageId": "ja",
            "name": "Business JA",
            "status": "published",
            "cards": [],
            "tags": ["business"],
        },
    )
    deck_id = resp.json()["id"]
    assert resp.json()["tags"] == ["business"]

    # Admin demotes (deletes) the tag — cascades to the deck.
    resp = client.delete(
        "/api/core/v1/admin/tags/business",
        headers={"X-Dev-User": "dev|admin-user"},
    )
    assert resp.status_code == 204, resp.text

    # Deck no longer reports the deleted tag.
    resp = client.get(f"/api/core/v1/decks/{deck_id}")
    assert resp.status_code == 200
    assert resp.json()["tags"] == []
