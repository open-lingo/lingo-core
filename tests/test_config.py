"""Config hard-guards (Fix 5) — DEBUG=true must NOT boot in a real
Lambda environment."""

import pytest

# Import the class once at module load (before any monkeypatches) so the
# module's own settings singleton boots in the dev environment.
from app.config import Settings


def test_debug_refused_in_lambda(monkeypatch: pytest.MonkeyPatch) -> None:
    """When AWS_LAMBDA_FUNCTION_NAME is set, DEBUG=true must raise."""
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "lingo-core-prod")
    monkeypatch.setenv("DEBUG", "true")

    with pytest.raises((RuntimeError, SystemExit, ValueError)):
        Settings()


def test_debug_allowed_outside_lambda(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without AWS_LAMBDA_FUNCTION_NAME, DEBUG=true must boot fine."""
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    monkeypatch.setenv("DEBUG", "true")

    s = Settings()
    assert s.DEBUG is True


def test_debug_false_allowed_in_lambda(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real Lambda deploy with DEBUG=false must boot."""
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "lingo-core-prod")
    monkeypatch.setenv("DEBUG", "false")

    s = Settings()
    assert s.DEBUG is False
