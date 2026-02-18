"""FastAPI dependency that validates an Auth0 JWT and returns the token payload.

In DEBUG mode, JWT validation is skipped entirely.  The user identity is
resolved from (in order):

1. ``X-Dev-User`` header  (override to impersonate any seeded user)
2. ``DEV_USER`` env var   (default dev identity)

This means the frontend can send its real Auth0 Bearer token and the
backend won't try to validate it — it just uses the dev identity.
"""

import logging
from typing import Annotated

import httpx
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.auth.schemas import TokenPayload
from app.config import settings

logger = logging.getLogger("lingo.auth")

_bearer = HTTPBearer(auto_error=False)

_jwks_cache: dict | None = None


def _dev_user_from_request(request: Request) -> TokenPayload | None:
    """In DEBUG mode, always return a dev identity — never fall through to JWT."""
    if not settings.DEBUG:
        return None
    dev_user = request.headers.get("X-Dev-User") or settings.DEV_USER
    if not dev_user:
        return None
    logger.debug("Dev auth bypass: sub=%s", dev_user)
    return TokenPayload(sub=dev_user, permissions=[])


async def _get_jwks() -> dict:
    """Fetch and cache the Auth0 JWKS (JSON Web Key Set)."""
    global _jwks_cache
    if _jwks_cache is not None:
        return _jwks_cache
    url = f"https://{settings.AUTH0_DOMAIN}/.well-known/jwks.json"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        resp.raise_for_status()
        _jwks_cache = resp.json()
        return _jwks_cache


def _find_rsa_key(jwks: dict, kid: str) -> dict | None:
    for key in jwks.get("keys", []):
        if key["kid"] == kid:
            return {k: key[k] for k in ("kty", "kid", "use", "n", "e")}
    return None


async def _validate_jwt(token: str) -> TokenPayload:
    """Validate a real Auth0 JWT and return the parsed claims."""
    try:
        unverified_header = jwt.get_unverified_header(token)
    except JWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token header")

    jwks = await _get_jwks()
    rsa_key = _find_rsa_key(jwks, unverified_header.get("kid", ""))

    if rsa_key is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unable to find signing key")

    try:
        payload = jwt.decode(
            token,
            rsa_key,
            algorithms=settings.AUTH0_ALGORITHMS,
            audience=settings.AUTH0_AUDIENCE,
            issuer=f"https://{settings.AUTH0_DOMAIN}/",
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token has expired")
    except JWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token validation failed")

    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token missing sub claim")

    return TokenPayload(sub=sub, permissions=payload.get("permissions", []))


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> TokenPayload:
    """Return the current user from a JWT or (in DEBUG mode) from X-Dev-User.

    Resolution order:
      1. ``X-Dev-User`` header (DEBUG only — skips JWT entirely)
      2. ``Authorization: Bearer <token>`` (Auth0 JWT validation)
      3. 401
    """
    dev = _dev_user_from_request(request)
    if dev is not None:
        return dev

    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")

    return await _validate_jwt(credentials.credentials)


async def get_current_user_optional(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> TokenPayload | None:
    """Return parsed token if present and valid; otherwise None."""
    dev = _dev_user_from_request(request)
    if dev is not None:
        return dev

    if credentials is None:
        return None

    try:
        return await _validate_jwt(credentials.credentials)
    except HTTPException:
        return None
