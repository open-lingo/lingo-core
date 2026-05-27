"""Tag API tests — public list, admin CRUD, slug validation, RBAC."""


def _make_admin(monkeypatch, admin_user_id: str) -> None:
    """Promote the seeded admin user via the env allowlist (same pattern as
    test_admin.py — bypasses needing a DB ``role='admin'``)."""
    from app.config import settings

    monkeypatch.setattr(settings, "ADMIN_USER_IDS", [admin_user_id])


# ── Public list ─────────────────────────────────────────────────────────────


def test_list_tags_empty_initial(api_client) -> None:
    client, _user_id, _ = api_client
    resp = client.get("/api/core/v1/tags")
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_create_then_list_tag(api_client, monkeypatch) -> None:
    client, _user_id, admin_user_id = api_client
    _make_admin(monkeypatch, admin_user_id)

    resp = client.post(
        "/api/core/v1/admin/tags",
        json={
            "slug": "jlpt-n5",
            "display_name": "JLPT N5",
            "description": "Beginner Japanese",
            "color": "#aabbcc",
        },
        headers={"X-Dev-User": "dev|admin-user"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["slug"] == "jlpt-n5"
    assert body["display_name"] == "JLPT N5"
    assert body["description"] == "Beginner Japanese"
    assert body["color"] == "#aabbcc"
    assert body.get("created_at")

    # Public list now contains the new tag.
    resp = client.get("/api/core/v1/tags")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["slug"] == "jlpt-n5"


# ── Slug validation ─────────────────────────────────────────────────────────


def test_create_tag_rejects_invalid_slug(api_client, monkeypatch) -> None:
    client, _user_id, admin_user_id = api_client
    _make_admin(monkeypatch, admin_user_id)

    for bad in ("JLPT-N5", "1leading", "a", "with space", "-leading-dash", "trailing!"):
        resp = client.post(
            "/api/core/v1/admin/tags",
            json={"slug": bad, "display_name": "x"},
            headers={"X-Dev-User": "dev|admin-user"},
        )
        assert resp.status_code == 422, f"expected 422 for slug={bad!r}; got {resp.status_code} ({resp.text})"


def test_create_tag_duplicate_returns_409(api_client, monkeypatch) -> None:
    client, _user_id, admin_user_id = api_client
    _make_admin(monkeypatch, admin_user_id)

    client.post(
        "/api/core/v1/admin/tags",
        json={"slug": "hiragana", "display_name": "Hiragana"},
        headers={"X-Dev-User": "dev|admin-user"},
    )
    resp = client.post(
        "/api/core/v1/admin/tags",
        json={"slug": "hiragana", "display_name": "Dupe"},
        headers={"X-Dev-User": "dev|admin-user"},
    )
    assert resp.status_code == 409, resp.text


# ── RBAC ────────────────────────────────────────────────────────────────────


def test_create_tag_non_admin_blocked(api_client) -> None:
    client, _user_id, _ = api_client
    resp = client.post(
        "/api/core/v1/admin/tags",
        json={"slug": "kanji", "display_name": "Kanji"},
    )
    assert resp.status_code == 403, resp.text


def test_update_tag_non_admin_blocked(api_client) -> None:
    client, _user_id, _ = api_client
    resp = client.patch(
        "/api/core/v1/admin/tags/kanji",
        json={"display_name": "Kanji 2"},
    )
    assert resp.status_code == 403, resp.text


def test_delete_tag_non_admin_blocked(api_client) -> None:
    client, _user_id, _ = api_client
    resp = client.delete("/api/core/v1/admin/tags/kanji")
    assert resp.status_code == 403, resp.text


# ── Patch + delete ──────────────────────────────────────────────────────────


def test_update_tag_round_trips(api_client, monkeypatch) -> None:
    client, _user_id, admin_user_id = api_client
    _make_admin(monkeypatch, admin_user_id)

    client.post(
        "/api/core/v1/admin/tags",
        json={"slug": "kdrama", "display_name": "K-Drama"},
        headers={"X-Dev-User": "dev|admin-user"},
    )
    resp = client.patch(
        "/api/core/v1/admin/tags/kdrama",
        json={"display_name": "Korean Drama", "color": "#112233"},
        headers={"X-Dev-User": "dev|admin-user"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["display_name"] == "Korean Drama"
    assert body["color"] == "#112233"


def test_update_tag_missing_returns_404(api_client, monkeypatch) -> None:
    client, _user_id, admin_user_id = api_client
    _make_admin(monkeypatch, admin_user_id)

    resp = client.patch(
        "/api/core/v1/admin/tags/nope",
        json={"display_name": "Nope"},
        headers={"X-Dev-User": "dev|admin-user"},
    )
    assert resp.status_code == 404, resp.text


def test_delete_tag(api_client, monkeypatch) -> None:
    client, _user_id, admin_user_id = api_client
    _make_admin(monkeypatch, admin_user_id)

    client.post(
        "/api/core/v1/admin/tags",
        json={"slug": "anime", "display_name": "Anime"},
        headers={"X-Dev-User": "dev|admin-user"},
    )
    resp = client.delete(
        "/api/core/v1/admin/tags/anime",
        headers={"X-Dev-User": "dev|admin-user"},
    )
    assert resp.status_code == 204, resp.text
    # And it's gone from the public list.
    resp = client.get("/api/core/v1/tags")
    assert resp.status_code == 200
    assert resp.json() == []


def test_delete_tag_missing_returns_404(api_client, monkeypatch) -> None:
    client, _user_id, admin_user_id = api_client
    _make_admin(monkeypatch, admin_user_id)

    resp = client.delete(
        "/api/core/v1/admin/tags/ghost",
        headers={"X-Dev-User": "dev|admin-user"},
    )
    assert resp.status_code == 404, resp.text
