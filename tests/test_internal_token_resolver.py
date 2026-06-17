"""INTERNAL_SERVICE_TOKEN resolution: env wins, SSM is the empty-env fallback.

Imports are deferred into each test so monkeypatch hits the live
``settings`` instance the resolver reads (conftest reloads app.config
mid-suite, replacing the singleton).
"""

import pytest


@pytest.fixture(autouse=True)
def _clear_ssm_cache():
    from app.auth.internal_token import _reset_cache_for_tests

    _reset_cache_for_tests()
    yield
    _reset_cache_for_tests()


def test_env_set_uses_env_and_skips_ssm(monkeypatch):
    from app.auth import internal_token
    from app.config import settings

    monkeypatch.setattr(settings, "INTERNAL_SERVICE_TOKEN", "dev-shared-secret")

    # Fail loud if the SSM path is touched while env is set.
    def _boom(_region):
        raise AssertionError("SSM must not be queried when env token is set")

    monkeypatch.setattr(internal_token, "_fetch_from_ssm", _boom)

    assert internal_token.resolve_internal_service_token() == "dev-shared-secret"


def test_env_empty_falls_back_to_ssm(monkeypatch):
    from app.auth import internal_token
    from app.config import settings

    monkeypatch.setattr(settings, "INTERNAL_SERVICE_TOKEN", "")

    calls = {"n": 0}

    def _fake_ssm(region):
        calls["n"] += 1
        assert region  # region threaded through from AWS_REGION setting
        return "ssm-prod-secret"

    monkeypatch.setattr(internal_token, "_fetch_from_ssm", _fake_ssm)

    assert internal_token.resolve_internal_service_token() == "ssm-prod-secret"
    # Second call is served from cache — SSM hit exactly once.
    assert internal_token.resolve_internal_service_token() == "ssm-prod-secret"
    assert calls["n"] == 1


def test_env_empty_ssm_via_moto(monkeypatch):
    """End-to-end through real boto3 against a moto-mocked SSM."""
    boto3 = pytest.importorskip("boto3")
    moto = pytest.importorskip("moto")

    from app.auth import internal_token
    from app.config import settings

    monkeypatch.setattr(settings, "INTERNAL_SERVICE_TOKEN", "")
    monkeypatch.setattr(settings, "AWS_REGION", "us-west-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")

    with moto.mock_aws():
        client = boto3.client("ssm", region_name="us-west-1")
        client.put_parameter(
            Name="/lingo/internal-service-token",
            Value="moto-secret",
            Type="SecureString",
        )
        assert internal_token.resolve_internal_service_token() == "moto-secret"


def test_env_empty_ssm_failure_returns_empty(monkeypatch):
    """No SSM param + empty env => empty string, no crash. Caller turns this into a 500."""
    import builtins

    from app.auth import internal_token
    from app.config import settings

    monkeypatch.setattr(settings, "INTERNAL_SERVICE_TOKEN", "")

    # Exercise the real best-effort _fetch_from_ssm wrapper by forcing the
    # boto3 import inside it to fail — it must swallow and return "".

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "boto3":
            raise ImportError("boto3 unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    assert internal_token.resolve_internal_service_token() == ""


def test_resolver_drives_dependency_gate(monkeypatch):
    """The require_internal_service gate honours the resolved (env) token."""
    from fastapi import HTTPException

    from app.auth.dependencies import require_internal_service
    from app.config import settings

    monkeypatch.setattr(settings, "INTERNAL_SERVICE_TOKEN", "dev-shared-secret")

    require_internal_service(authorization="Bearer dev-shared-secret")  # no raise

    with pytest.raises(HTTPException) as exc:
        require_internal_service(authorization="Bearer wrong")
    assert exc.value.status_code == 401
