"""FastAPI dependency that validates an Auth0 JWT and returns the token payload."""

from functools import lru_cache
from typing import Annotated

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.auth.schemas import TokenPayload
from app.config import settings

_bearer = HTTPBearer()

_jwks_cache: dict | None = None


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


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
) -> TokenPayload:
    """Validate the Bearer token against Auth0 and return parsed claims.

    Raises 401 if the token is missing, expired, or has an invalid signature.
    """
    token = credentials.credentials

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

    return TokenPayload(
        sub=sub,
        permissions=payload.get("permissions", []),
    )
