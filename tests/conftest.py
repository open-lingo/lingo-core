"""Shared test fixtures for the lingo-core API.

Uses an isolated temporary SQLite database per test. Bypasses Auth0 via
``DEBUG=true`` + ``X-Dev-User`` header so we can drive routes through the
FastAPI ``TestClient`` without a real JWT.
"""

import os
import tempfile
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio


@pytest.fixture()
def tmp_db_path() -> Iterator[str]:
    """Per-test SQLite file. Cleaned up after the test."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="lingo-test-")
    os.close(fd)
    try:
        yield path
    finally:
        try:
            Path(path).unlink()
        except FileNotFoundError:
            pass


@pytest_asyncio.fixture()
async def sqlite_user_repo(tmp_db_path: str) -> AsyncIterator:
    from app.db.sqlite.user import SqliteUserRepository

    repo = SqliteUserRepository(tmp_db_path)
    await repo.connect()
    try:
        yield repo
    finally:
        await repo.close()


@pytest_asyncio.fixture()
async def sqlite_srs_repo(tmp_db_path: str) -> AsyncIterator:
    from app.db.sqlite.srs import SqliteSRSRepository

    repo = SqliteSRSRepository(tmp_db_path)
    await repo.connect()
    try:
        yield repo
    finally:
        await repo.close()


@pytest_asyncio.fixture()
async def sqlite_progress_repo(tmp_db_path: str) -> AsyncIterator:
    from app.db.sqlite.progress import SqliteProgressRepository

    repo = SqliteProgressRepository(tmp_db_path)
    await repo.connect()
    try:
        yield repo
    finally:
        await repo.close()


@pytest_asyncio.fixture()
async def api_client(tmp_db_path: str, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator:
    """FastAPI test client with DEBUG=true (Auth0 bypassed) and a clean DB.

    Yields ``(client, user_id, admin_user_id)`` where both users are pre-seeded
    so admin and non-admin paths can both be exercised.
    """
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("SQLITE_PATH", tmp_db_path)
    monkeypatch.setenv("DEBUG", "true")
    # Use placeholder dev user that we'll register below.
    monkeypatch.setenv("DEV_USER", "dev|test-user")

    # Reload settings + provider so they pick up the new env. ``auth.dependencies``
    # also imports ``settings`` at module load — reload it too so the dev-user
    # bypass picks up the test DEV_USER instead of any prior value.
    import importlib

    from app import config as config_mod

    importlib.reload(config_mod)
    from app.db import provider as provider_mod

    importlib.reload(provider_mod)
    from app.auth import dependencies as auth_dep_mod

    importlib.reload(auth_dep_mod)
    from app import main as main_mod

    importlib.reload(main_mod)

    app = main_mod.app
    with TestClient(app) as client:
        # Register the default dev user via the API (so internal UUID exists).
        username = f"dev{uuid.uuid4().hex[:6]}"
        resp = client.post(
            "/api/core/v1/users/me",
            json={"username": username, "display_name": "Dev User"},
        )
        assert resp.status_code == 201, resp.text
        user_id = resp.json()["id"]

        # Register a second user as the "admin" identity.
        admin_username = f"adm{uuid.uuid4().hex[:6]}"
        resp = client.post(
            "/api/core/v1/users/me",
            json={"username": admin_username, "display_name": "Admin"},
            headers={"X-Dev-User": "dev|admin-user"},
        )
        assert resp.status_code == 201, resp.text
        admin_user_id = resp.json()["id"]

        yield client, user_id, admin_user_id
