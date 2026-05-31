"""Service-to-service bearer auth for /quests/_internal routes."""

import pytest
from fastapi import HTTPException

from app.auth.dependencies import require_internal_service
from app.config import settings


def test_rejects_missing_header(monkeypatch):
    monkeypatch.setattr(settings, "INTERNAL_SERVICE_TOKEN", "secret-x")
    with pytest.raises(HTTPException) as exc_info:
        require_internal_service(authorization=None)
    assert exc_info.value.status_code == 401


def test_rejects_wrong_token(monkeypatch):
    monkeypatch.setattr(settings, "INTERNAL_SERVICE_TOKEN", "secret-x")
    with pytest.raises(HTTPException) as exc_info:
        require_internal_service(authorization="Bearer not-it")
    assert exc_info.value.status_code == 401


def test_rejects_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "INTERNAL_SERVICE_TOKEN", "")
    with pytest.raises(HTTPException) as exc_info:
        require_internal_service(authorization="Bearer anything")
    assert exc_info.value.status_code == 500


def test_accepts_matching_token(monkeypatch):
    monkeypatch.setattr(settings, "INTERNAL_SERVICE_TOKEN", "secret-x")
    # Should not raise.
    require_internal_service(authorization="Bearer secret-x")
