"""Provider degraded-mode tests (Fix 6) — a failing repo at startup must
NOT crash the whole Lambda; affected domains return 503; ``/health`` and
other domains keep working."""

import importlib
import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture()
def boot_partial_init_failure(monkeypatch: pytest.MonkeyPatch):
    """Boot the app with one repo's connect() forced to raise.

    Returns a TestClient whose ``progress`` repo failed init.
    """
    from fastapi.testclient import TestClient

    # Pre-empt the failure: stub the SqliteProgressRepository before init.
    fd, tmp_db = tempfile.mkstemp(suffix=".db", prefix="lingo-test-")
    os.close(fd)

    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("SQLITE_PATH", tmp_db)
    monkeypatch.setenv("DEBUG", "true")

    from app import config as config_mod
    importlib.reload(config_mod)

    from app.db.sqlite import progress as progress_mod

    async def failing_connect(self) -> None:  # noqa: ANN001
        raise RuntimeError("simulated progress repo init failure")

    monkeypatch.setattr(
        progress_mod.SqliteProgressRepository, "connect", failing_connect
    )

    from app.db import provider as provider_mod
    importlib.reload(provider_mod)
    from app.auth import dependencies as auth_dep_mod
    importlib.reload(auth_dep_mod)
    from app import main as main_mod
    importlib.reload(main_mod)

    try:
        with TestClient(main_mod.app) as client:
            yield client
    finally:
        try:
            Path(tmp_db).unlink()
        except FileNotFoundError:
            pass


def test_partial_init_failure_does_not_crash(boot_partial_init_failure) -> None:
    """The app must boot even when one repo's connect() raises."""
    client = boot_partial_init_failure
    # /health must still be OK.
    resp = client.get("/health")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "ok"


def test_degraded_domain_returns_503(boot_partial_init_failure) -> None:
    """Endpoints in the failed domain return 503 with a clear message."""
    client = boot_partial_init_failure
    # Register user first (users repo is healthy)
    import uuid

    resp = client.post(
        "/api/core/v1/users/me",
        json={"username": f"u{uuid.uuid4().hex[:6]}", "display_name": "Dev"},
    )
    assert resp.status_code == 201, resp.text

    # Then hit the progress domain — should 503, not 500/AssertionError.
    resp = client.get("/api/core/v1/progress/me")
    assert resp.status_code == 503, resp.text


def test_other_domains_still_work_in_degraded_mode(boot_partial_init_failure) -> None:
    """Domains whose repo init succeeded must keep responding normally."""
    client = boot_partial_init_failure
    import uuid

    resp = client.post(
        "/api/core/v1/users/me",
        json={"username": f"u{uuid.uuid4().hex[:6]}", "display_name": "Dev"},
    )
    assert resp.status_code == 201, resp.text

    # SRS state endpoint should still work (SRS repo init succeeded).
    resp = client.get("/api/core/v1/srs/state")
    assert resp.status_code == 200, resp.text
