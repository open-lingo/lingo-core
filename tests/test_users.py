"""Users API tests — focused on the public discover surface that powers
the find-friends browser and the (rewritten) contributors page.

Uses the same DEBUG=true + X-Dev-User shortcut as test_social.py so we
don't need a real Auth0 token. Each test class boots a fresh module-scoped
TestClient against an isolated temp SQLite DB.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any

import pytest
from fastapi.testclient import TestClient

# Env must be set BEFORE app.config import; settings is module-level.
TMP_DB = os.path.join(tempfile.mkdtemp(prefix="lingo-users-"), "users.db")
os.environ["DB_BACKEND"] = "sqlite"
os.environ["SQLITE_PATH"] = TMP_DB
os.environ["DEBUG"] = "true"
os.environ["DEV_USER"] = "auth0|trevor_t"


# ── Helpers ──────────────────────────────────────────────────────────────────


def _register_user(
    client: TestClient, sub: str, username: str, display_name: str
) -> dict[str, Any]:
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


def _as(sub: str) -> dict[str, str]:
    return {"X-Dev-User": sub}


def _set_learning(client: TestClient, sub: str, lang: str) -> None:
    resp = client.patch(
        "/api/core/v1/users/me/settings",
        json={
            "learningLanguage": lang,
            "learning": {"learningLanguageId": lang, "uiLocale": "en"},
        },
        headers=_as(sub),
    )
    assert resp.status_code == 200, resp.text


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def client() -> Any:
    # Reload config + provider + auth deps + main so previously-imported
    # modules pick up our env. Needed when a sibling test (e.g.
    # test_provider.py) reloaded with different DEBUG/DB settings — the
    # global ``settings`` instance otherwise sticks at whatever the last
    # test left it.
    import importlib

    os.environ["DB_BACKEND"] = "sqlite"
    os.environ["SQLITE_PATH"] = TMP_DB
    os.environ["DEBUG"] = "true"
    os.environ["DEV_USER"] = "auth0|trevor_t"

    from app import config as config_mod

    importlib.reload(config_mod)
    from app.db import provider as provider_mod

    importlib.reload(provider_mod)
    from app.auth import dependencies as auth_dep_mod

    importlib.reload(auth_dep_mod)
    from app import main as main_mod

    importlib.reload(main_mod)

    with TestClient(main_mod.app) as c:
        yield c


@pytest.fixture(scope="module")
def users(client: TestClient) -> dict[str, dict[str, Any]]:
    trevor = _register_user(client, "auth0|trevor_t", "trevor_t", "Trevor T")
    ken = _register_user(client, "auth0|kenji_t", "kenji_t", "Kenji T")
    sora = _register_user(client, "auth0|sora_t", "sora_t", "Sora T")
    mai = _register_user(client, "auth0|mai_t", "mai_t", "Mai T")
    diego = _register_user(client, "auth0|diego_t", "diego_t", "Diego T")

    _set_learning(client, "auth0|trevor_t", "ja")
    _set_learning(client, "auth0|kenji_t", "ja")
    _set_learning(client, "auth0|sora_t", "ja")
    _set_learning(client, "auth0|mai_t", "ja")
    _set_learning(client, "auth0|diego_t", "es")

    return {"trevor": trevor, "kenji": ken, "sora": sora, "mai": mai, "diego": diego}


# ── /users/discover — happy path ─────────────────────────────────────────────


def test_discover_default_excludes_self(
    client: TestClient, users: dict[str, dict[str, Any]]
) -> None:
    resp = client.get(
        "/api/core/v1/users/discover", headers=_as("auth0|trevor_t")
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "users" in body and "total" in body and "has_more" in body
    usernames = {u["username"] for u in body["users"]}
    assert "trevor_t" not in usernames, "self should be excluded when q is empty"
    # The four other seeded users should be visible.
    assert {"kenji_t", "sora_t", "mai_t", "diego_t"}.issubset(usernames)
    # PublicUserSummary shape.
    for u in body["users"]:
        assert "auth0_id" in u
        assert "user_id" in u
        assert "friendship_status" in u
        assert u["friendship_status"] in {
            "self",
            "friend",
            "request_in",
            "request_out",
            "blocked",
            "none",
        }


def test_discover_filters_by_substring(
    client: TestClient, users: dict[str, dict[str, Any]]
) -> None:
    resp = client.get(
        "/api/core/v1/users/discover",
        params={"q": "ken"},
        headers=_as("auth0|trevor_t"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    usernames = {u["username"] for u in body["users"]}
    assert usernames == {"kenji_t"}, usernames


def test_discover_filters_by_lang(
    client: TestClient, users: dict[str, dict[str, Any]]
) -> None:
    resp = client.get(
        "/api/core/v1/users/discover",
        params={"lang": "es"},
        headers=_as("auth0|trevor_t"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    usernames = {u["username"] for u in body["users"]}
    assert usernames == {"diego_t"}, usernames

    resp = client.get(
        "/api/core/v1/users/discover",
        params={"lang": "ja"},
        headers=_as("auth0|trevor_t"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    usernames = {u["username"] for u in body["users"]}
    # Trevor self is excluded; Kenji/Sora/Mai all learn ja.
    assert usernames == {"kenji_t", "sora_t", "mai_t"}, usernames


def test_discover_q_allows_self(
    client: TestClient, users: dict[str, dict[str, Any]]
) -> None:
    resp = client.get(
        "/api/core/v1/users/discover",
        params={"q": "trevor"},
        headers=_as("auth0|trevor_t"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    usernames = [u["username"] for u in body["users"]]
    assert "trevor_t" in usernames
    me = next(u for u in body["users"] if u["username"] == "trevor_t")
    assert me["friendship_status"] == "self"


def test_discover_excludes_blocked(
    client: TestClient, users: dict[str, dict[str, Any]]
) -> None:
    diego_id = users["diego"]["id"]
    resp = client.post(
        f"/api/core/v1/social/blocks/{diego_id}",
        headers=_as("auth0|trevor_t"),
    )
    assert resp.status_code == 200, resp.text

    resp = client.get(
        "/api/core/v1/users/discover", headers=_as("auth0|trevor_t")
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    usernames = {u["username"] for u in body["users"]}
    assert "diego_t" not in usernames

    # Cleanup so this test stays independent.
    resp = client.delete(
        f"/api/core/v1/social/blocks/{diego_id}",
        headers=_as("auth0|trevor_t"),
    )
    assert resp.status_code == 204


def test_discover_pagination(
    client: TestClient, users: dict[str, dict[str, Any]]
) -> None:
    resp = client.get(
        "/api/core/v1/users/discover",
        params={"limit": 2, "offset": 0},
        headers=_as("auth0|trevor_t"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["users"]) == 2
    assert body["total"] >= 4
    assert body["has_more"] is True

    resp = client.get(
        "/api/core/v1/users/discover",
        params={"limit": 100, "offset": 0},
        headers=_as("auth0|trevor_t"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_more"] is False
