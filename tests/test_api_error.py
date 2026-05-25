"""Sad-path coverage for api_error / require_repo helpers and the routers
that adopted them in Phase 1+2.

The integration tests use FastAPI's dependency_overrides to swap in an
AsyncMock repo that raises arbitrary exceptions, so we can assert the
wrapper turns those into a clean 500 with the expected ``Error <context>``
detail instead of leaking the underlying exception or a 5xx with traceback.
"""

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.auth.dependencies import get_current_user, get_registered_user
from app.auth.schemas import TokenPayload
from app.db.provider import get_deck_repo, get_story_repo
from app.decks.router import router as decks_router
from app.shared.errors import api_error
from app.shared.repos import require_repo
from app.stories.router import router as stories_router

# ── Unit tests on the helpers ───────────────────────────────────────────────


def test_api_error_passes_http_exception_through() -> None:
    """HTTPException should not be wrapped — it's a deliberate error contract."""
    with pytest.raises(HTTPException) as exc:
        with api_error("doing the thing"):
            raise HTTPException(status_code=404, detail="not found")
    assert exc.value.status_code == 404
    assert exc.value.detail == "not found"


def test_api_error_wraps_unknown_exception_as_500() -> None:
    with pytest.raises(HTTPException) as exc:
        with api_error("creating widget"):
            raise RuntimeError("boom")
    assert exc.value.status_code == 500
    assert exc.value.detail == "Error creating widget"


def test_api_error_chains_original_exception() -> None:
    """The original exception should remain in __cause__ for log forensics."""
    with pytest.raises(HTTPException) as exc:
        with api_error("doing X"):
            raise ValueError("the real cause")
    assert isinstance(exc.value.__cause__, ValueError)
    assert str(exc.value.__cause__) == "the real cause"


def test_require_repo_returns_repo_when_present() -> None:
    sentinel = object()
    assert require_repo(sentinel, "decks") is sentinel


def test_require_repo_raises_503_when_none() -> None:
    with pytest.raises(HTTPException) as exc:
        require_repo(None, "decks")
    assert exc.value.status_code == 503
    assert "decks" in exc.value.detail


# ── Integration tests per refactored router ─────────────────────────────────


def _fake_user() -> TokenPayload:
    return TokenPayload(sub="dev|tester", permissions=[], id="user-test-uuid")


def _build_app(router, prefix: str, repo_dep, repo_mock) -> FastAPI:
    """Build a minimal FastAPI app with the given router and overrides."""
    app = FastAPI()
    app.include_router(router, prefix=prefix)
    app.dependency_overrides[get_current_user] = _fake_user
    app.dependency_overrides[get_registered_user] = _fake_user
    app.dependency_overrides[repo_dep] = lambda: repo_mock
    return app


def test_stories_browse_wraps_repo_failure_as_500() -> None:
    """When the story repo raises, /stories/browse returns 500 with our context."""
    repo = AsyncMock()
    repo.list_stories.side_effect = RuntimeError("dynamo timeout")
    app = _build_app(stories_router, "/stories", get_story_repo, repo)
    client = TestClient(app)
    resp = client.get("/stories/browse")
    assert resp.status_code == 500
    assert resp.json()["detail"] == "Error listing published stories"


def test_stories_browse_returns_503_when_repo_not_configured() -> None:
    """require_repo should surface a 503 when the story repo is None."""
    app = _build_app(stories_router, "/stories", get_story_repo, None)
    # Override the None case manually
    app.dependency_overrides[get_story_repo] = lambda: None
    client = TestClient(app)
    resp = client.get("/stories/browse")
    assert resp.status_code == 503
    assert "stories" in resp.json()["detail"]


def test_decks_list_wraps_repo_failure_as_500() -> None:
    """When the deck repo raises during list, /decks returns 500 with our context."""
    repo = AsyncMock()
    repo.list_owned_manifests.side_effect = ConnectionError("db down")
    app = _build_app(decks_router, "/decks", get_deck_repo, repo)
    client = TestClient(app)
    resp = client.get("/decks")
    assert resp.status_code == 500
    assert resp.json()["detail"] == "Error listing owned decks"


def test_decks_get_wraps_repo_failure_as_500() -> None:
    """When the deck repo raises during a single fetch, /decks/{id} returns 500."""
    repo = AsyncMock()
    repo.get_deck.side_effect = RuntimeError("read failed")
    app = _build_app(decks_router, "/decks", get_deck_repo, repo)
    client = TestClient(app)
    resp = client.get("/decks/some-id")
    assert resp.status_code == 500
    assert resp.json()["detail"] == "Error fetching deck"


def test_decks_get_returns_404_when_not_found() -> None:
    """When the deck repo returns None, the 404 (not the api_error 500) wins."""
    repo = AsyncMock()
    repo.get_deck.return_value = None
    app = _build_app(decks_router, "/decks", get_deck_repo, repo)
    client = TestClient(app)
    resp = client.get("/decks/some-id")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Deck not found"
