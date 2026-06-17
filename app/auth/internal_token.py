"""Resolves the INTERNAL_SERVICE_TOKEN used to VALIDATE inbound internal
callbacks from lingo-async (e.g. /quests/_internal/{id}/progress).

Precedence (env wins for local/dev/test determinism):

1. ``settings.INTERNAL_SERVICE_TOKEN`` (sourced from the env var / .env).
   If non-empty, it is returned verbatim and SSM is never touched.
2. Otherwise, the SecureString SSM parameter ``/lingo/internal-service-token``
   is fetched once and cached in-process. This is the prod source when the
   env var is left empty in the Lambda config.

ANY SSM failure (no boto3, no creds, AccessDenied, ParameterNotFound, ...)
falls back to the empty env value — the caller treats empty as "not
configured" and returns a 500, so we never crash the request path here.

The cache is keyed only on "did SSM succeed once" — we re-read the env
setting on every call so the conftest module-reload / monkeypatch pattern
keeps working without a manual cache reset.
"""

import logging

logger = logging.getLogger("lingo.auth")

# Module-level cache for the SSM-sourced value. ``None`` means "not yet
# fetched from SSM"; a string (possibly empty after a failure) means we
# have attempted the fetch and should not hit SSM again this process.
_ssm_cached: str | None = None

_PARAM_NAME = "/lingo/internal-service-token"  # noqa: S105 — parameter name, not a secret


def _fetch_from_ssm(region: str) -> str:
    """Best-effort SSM fetch. Returns the decrypted value, or "" on any failure."""
    try:
        import boto3  # local import: only pulled in on the SSM path (prod)

        client = boto3.client("ssm", region_name=region)
        resp = client.get_parameter(Name=_PARAM_NAME, WithDecryption=True)
        return resp["Parameter"]["Value"]
    except Exception as exc:  # noqa: BLE001 — best-effort; env fallback is the contract
        logger.warning("SSM fetch of %s failed (%s); falling back to env", _PARAM_NAME, exc)
        return ""


def resolve_internal_service_token() -> str:
    """Return the internal-service token. Env wins; SSM is the empty-env fallback."""
    from app.config import settings as live_settings

    env_token = live_settings.INTERNAL_SERVICE_TOKEN
    if env_token:
        return env_token

    global _ssm_cached
    if _ssm_cached is None:
        _ssm_cached = _fetch_from_ssm(live_settings.AWS_REGION or "us-west-1")
    return _ssm_cached


def _reset_cache_for_tests() -> None:
    """Test-only: clear the SSM cache between cases."""
    global _ssm_cached
    _ssm_cached = None
