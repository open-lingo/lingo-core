"""FastAPI dependency that validates an Auth0 JWT and returns the token payload.

After JWT validation the auth0 ``sub`` is resolved to our internal user UUID
via the UserRepository.  All domain code uses ``user.id`` (the UUID); only
auth-specific code (registration, JWT validation) touches ``user.sub``.

In DEBUG mode, JWT validation is skipped entirely.  The user identity is
resolved from (in order):

1. ``X-Dev-User`` header  (override to impersonate any seeded user)
2. ``DEV_USER`` env var   (default dev identity)
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


def _to_rsa_key(key: dict) -> dict | None:
    """Extract RSA public key for jwt.decode. Returns None if not valid RSA."""
    if key.get("kty") != "RSA" or "n" not in key or "e" not in key:
        return None
    return {k: key[k] for k in ("kty", "kid", "use", "n", "e") if k in key}


def _get_rsa_keys(jwks: dict, kid: str) -> list[dict]:
    """Return RSA keys from JWKS. Prefer exact kid match; else all RSA keys for fallback."""
    keys = jwks.get("keys", [])
    match = None
    all_rsa = []
    for key in keys:
        rk = _to_rsa_key(key)
        if rk is None:
            continue
        all_rsa.append(rk)
        if kid and key.get("kid") == kid:
            match = rk
    return [match] if match else all_rsa


async def _validate_jwt(token: str) -> TokenPayload:
    """Validate a real Auth0 JWT and return the parsed claims."""
    try:
        unverified_header = jwt.get_unverified_header(token)
    except JWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token header")

    kid = unverified_header.get("kid", "")
    jwks = await _get_jwks()
    rsa_keys = _get_rsa_keys(jwks, kid)

    if not rsa_keys:
        logger.warning("JWKS has no RSA keys: domain=%s", settings.AUTH0_DOMAIN)
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Unable to find signing key — AUTH0_DOMAIN may be wrong",
        )

    issuer = f"https://{settings.AUTH0_DOMAIN}/"

    for rsa_key in rsa_keys:
        try:
            payload = jwt.decode(
                token,
                rsa_key,
                algorithms=settings.AUTH0_ALGORITHMS,
                audience=settings.AUTH0_AUDIENCE,
                issuer=issuer,
            )
            sub = payload.get("sub")
            if not sub:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token missing sub claim")
            return TokenPayload(sub=sub, permissions=payload.get("permissions", []))
        except jwt.ExpiredSignatureError:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token has expired")
        except HTTPException:
            raise
        except JWTError:
            continue

    logger.warning(
        "JWT validation failed: kid=%r, domain=%s, jwks_kids=%s",
        kid, settings.AUTH0_DOMAIN,
        [k.get("kid") for k in jwks.get("keys", [])],
    )
    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Token validation failed — check AUTH0_DOMAIN and AUTH0_AUDIENCE match your Auth0 app",
    )


async def _resolve_user_id(token: TokenPayload) -> TokenPayload:
    """Look up the internal user UUID for this auth0 sub and attach it."""
    from app.db.provider import get_user_repo

    repo = get_user_repo()
    if repo is None:
        return token
    user = await repo.get_user_by_auth0_id(token.sub)
    if user:
        return token.model_copy(update={"id": user["id"]})
    return token


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> TokenPayload:
    """Return the current user with internal UUID resolved.

    Resolution order:
      1. ``X-Dev-User`` header (DEBUG only — skips JWT entirely)
      2. ``Authorization: Bearer <token>`` (Auth0 JWT validation)
      3. 401

    ``token.id`` is set if the user exists in our DB; None for unregistered users.
    """
    dev = _dev_user_from_request(request)
    token = dev if dev is not None else None

    if token is None:
        if credentials is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
        token = await _validate_jwt(credentials.credentials)

    return await _resolve_user_id(token)


async def get_current_user_optional(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> TokenPayload | None:
    """Return parsed token with UUID if present and valid; otherwise None."""
    dev = _dev_user_from_request(request)
    token = dev if dev is not None else None

    if token is None:
        if credentials is None:
            return None
        try:
            token = await _validate_jwt(credentials.credentials)
        except HTTPException:
            return None

    return await _resolve_user_id(token)


async def get_registered_user(
    user: Annotated[TokenPayload, Depends(get_current_user)],
) -> TokenPayload:
    """Like get_current_user but 404s if the user hasn't completed registration.
    Also blocks banned users with 403 USER_BANNED.
    """
    if user.id is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "User not registered — complete registration first",
        )
    from app.auth.ban import raise_if_user_banned
    from app.db.provider import get_user_repo

    repo = get_user_repo()
    if repo:
        record = await repo.get_user_by_id(user.id)
        if record:
            raise_if_user_banned(record)
    return user


async def require_admin(
    user: Annotated[TokenPayload, Depends(get_registered_user)],
) -> TokenPayload:
    """Require admin or super_admin role. Fetches user record to check role."""
    from app.auth.roles import has_admin_access
    from app.db.provider import get_user_repo

    repo = get_user_repo()
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="User storage not configured",
        )
    record = await repo.get_user_by_id(user.id)
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if not has_admin_access(record.get("role") or "user"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


async def get_community_user(
    user: Annotated[TokenPayload, Depends(get_registered_user)],
) -> TokenPayload:
    """Like get_registered_user but also blocks community-banned users with 403 COMMUNITY_BANNED."""
    from app.auth.ban import raise_if_community_banned
    from app.db.provider import get_user_repo

    repo = get_user_repo()
    if repo:
        record = await repo.get_user_by_id(user.id)
        if record:
            raise_if_community_banned(record)
    return user


async def get_community_user_optional(
    user: Annotated[TokenPayload | None, Depends(get_current_user_optional)],
) -> TokenPayload | None:
    """Like get_current_user_optional but raises 403 COMMUNITY_BANNED if user is community-banned."""
    if user is None or user.id is None:
        return user
    from app.auth.ban import raise_if_community_banned
    from app.db.provider import get_user_repo

    repo = get_user_repo()
    if repo:
        record = await repo.get_user_by_id(user.id)
        if record:
            raise_if_community_banned(record)
    return user
