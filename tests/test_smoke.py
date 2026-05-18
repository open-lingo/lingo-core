"""Minimal CI smoke checks — no DB or network."""

from app.main import app


def test_fastapi_app_metadata() -> None:
    assert app.title == "Lingo Core API"
    assert app.version == "0.1.0"
