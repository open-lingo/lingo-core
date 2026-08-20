"""Surface-mode allowlist: the beta deployment HTTP-exposes only the
landing/sign-in/learn/practice core loop (boot, users, srs, progress) and
un-mounts every other router, shrinking the unauthenticated + authenticated
attack surface. Full mode (default) is unchanged.

Un-mounting removes only the HTTP exposure; the handler functions stay
importable, which matters because /boot calls quests/progress/users/srs
handlers internally.
"""

from __future__ import annotations


def _paths(mode: str) -> set[str]:
    # FastAPI flattens included routers lazily; ``app.openapi()`` forces full
    # resolution and returns exactly the path table the app serves.
    from fastapi import FastAPI

    from app.v1.router import build_v1_router

    app = FastAPI()
    app.include_router(build_v1_router(mode))
    return set(app.openapi()["paths"].keys())


def test_beta_mode_keeps_core_loop() -> None:
    beta = _paths("beta")
    assert any(p.startswith("/boot") for p in beta)
    assert any(p.startswith("/users") for p in beta)
    assert any(p.startswith("/srs") for p in beta)
    assert any(p.startswith("/progress") for p in beta)


def test_beta_mode_unmounts_high_risk_routers() -> None:
    beta = _paths("beta")
    # The scan-backed public reads live under /community and /tags — gone.
    assert not any(p.startswith("/community") for p in beta)
    assert not any(p.startswith("/tags") for p in beta)
    assert not any(p.startswith("/admin") for p in beta)
    assert not any(p.startswith("/social") for p in beta)
    assert not any(p.startswith("/decks") for p in beta)
    assert not any(p.startswith("/stories") for p in beta)
    assert not any(p.startswith("/ads") for p in beta)


def test_full_mode_mounts_everything() -> None:
    full = _paths("full")
    assert any(p.startswith("/community") for p in full)
    assert any(p.startswith("/admin") for p in full)
    assert any(p.startswith("/social") for p in full)
    assert any(p.startswith("/srs") for p in full)


def test_unknown_mode_falls_back_to_full() -> None:
    # A typo in the env var must fail OPEN to full, never silently serve an
    # empty API (which would look like a total outage) — surface reduction is
    # opt-in and explicit.
    assert _paths("wat") == _paths("full")
