"""Service-to-service bearer auth for /quests/_internal routes.

The api_client conftest fixture reloads ``app.config``, which creates a
new ``settings`` singleton mid-suite. Imports are deferred into each
test so monkeypatch hits the live instance the dep actually reads.
"""

import pytest
from fastapi import HTTPException


def test_rejects_missing_header(monkeypatch):
    from app.config import settings
    from app.auth.dependencies import require_internal_service

    monkeypatch.setattr(settings, "INTERNAL_SERVICE_TOKEN", "secret-x")
    with pytest.raises(HTTPException) as exc_info:
        require_internal_service(authorization=None)
    assert exc_info.value.status_code == 401


def test_rejects_wrong_token(monkeypatch):
    from app.config import settings
    from app.auth.dependencies import require_internal_service

    monkeypatch.setattr(settings, "INTERNAL_SERVICE_TOKEN", "secret-x")
    with pytest.raises(HTTPException) as exc_info:
        require_internal_service(authorization="Bearer not-it")
    assert exc_info.value.status_code == 401


def test_rejects_when_unconfigured(monkeypatch):
    from app.config import settings
    from app.auth.dependencies import require_internal_service

    monkeypatch.setattr(settings, "INTERNAL_SERVICE_TOKEN", "")
    with pytest.raises(HTTPException) as exc_info:
        require_internal_service(authorization="Bearer anything")
    assert exc_info.value.status_code == 500


def test_accepts_matching_token(monkeypatch):
    from app.config import settings
    from app.auth.dependencies import require_internal_service

    monkeypatch.setattr(settings, "INTERNAL_SERVICE_TOKEN", "secret-x")
    # Should not raise.
    require_internal_service(authorization="Bearer secret-x")
