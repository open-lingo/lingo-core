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
import time
from typing import Annotated

import httpx
from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.auth.schemas import TokenPayload
from app.config import settings

logger = logging.getLogger("lingo.auth")

_bearer = HTTPBearer(auto_error=False)

_jwks_cache: dict | None = None
# Fix 7 — TTL-driven JWKS refresh. ``_jwks_cache_at`` is the epoch seconds of
# the last successful fetch; ``_jwks_last_refresh`` rate-limits kid-miss
# refreshes so a flood of bad tokens can't DoS Auth0 (HTTP 429).
_jwks_cache_at: float = 0.0
_jwks_last_refresh: float = 0.0
_JWKS_TTL_SEC = 3600  # 1 hour
_JWKS_REFRESH_MIN_INTERVAL_SEC = 60  # at most 1 force-refresh per minute


def _dev_user_from_request(request: Request) -> TokenPayload | None:
    """In DEBUG mode, always return a dev identity — never fall through to JWT."""
    if not settings.DEBUG:
        return None
    dev_user = request.headers.get("X-Dev-User") or settings.DEV_USER
    if not dev_user:
        return None
    logger.debug("Dev auth bypass: sub=%s", dev_user)
    return TokenPayload(sub=dev_user, permissions=[])


async def _fetch_jwks() -> dict:
    """Unconditional fetch — caller decides whether the cache is stale."""
    url = f"https://{settings.AUTH0_DOMAIN}/.well-known/jwks.json"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


async def _get_jwks() -> dict:
    """Return cached JWKS, refreshing if older than the TTL."""
    global _jwks_cache, _jwks_cache_at
    now = time.time()
    if _jwks_cache is not None and (now - _jwks_cache_at) < _JWKS_TTL_SEC:
        return _jwks_cache
    _jwks_cache = await _fetch_jwks()
    _jwks_cache_at = now
    return _jwks_cache


def _exact_kid_present(jwks: dict, kid: str) -> bool:
    if not kid:
        return False
    for key in jwks.get("keys", []):
        if key.get("kid") == kid and _to_rsa_key(key) is not None:
            return True
    return False


async def _get_rsa_keys_with_refresh(kid: str) -> list[dict]:
    """Return RSA keys for ``kid``. On exact-kid miss, refresh JWKS once
    (rate-limited to once a minute to avoid Auth0 429)."""
    global _jwks_cache, _jwks_cache_at, _jwks_last_refresh
    jwks = await _get_jwks()
    if _exact_kid_present(jwks, kid):
        return _get_rsa_keys(jwks, kid)

    now = time.time()
    if now - _jwks_last_refresh < _JWKS_REFRESH_MIN_INTERVAL_SEC:
        return _get_rsa_keys(jwks, kid)
    _jwks_last_refresh = now
    try:
        _jwks_cache = await _fetch_jwks()
        _jwks_cache_at = now
    except Exception:  # noqa: BLE001
        return _get_rsa_keys(jwks, kid)
    return _get_rsa_keys(_jwks_cache, kid)


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
    rsa_keys = await _get_rsa_keys_with_refresh(kid)
    jwks = _jwks_cache or {}

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
        kid,
        settings.AUTH0_DOMAIN,
        [k.get("kid") for k in jwks.get("keys", [])],
    )
    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Token validation failed — check AUTH0_DOMAIN and AUTH0_AUDIENCE match your Auth0 app",
    )


# Fix 8 — in-process LRU for auth0_sub → internal user_id. Each authed
# request paid for a get_user_by_auth0_id call before; on Dynamo that's a
# GSI query per request. 5-minute TTL is the same shape as the JWKS cache.
_USER_ID_CACHE_TTL_SEC = 300
_user_id_cache: dict[str, tuple[str, float]] = {}


def invalidate_user_id_cache(auth0_sub: str | None = None) -> None:
    """Drop one or all entries from the cache (call on user delete)."""
    if auth0_sub is None:
        _user_id_cache.clear()
    else:
        _user_id_cache.pop(auth0_sub, None)


async def _resolve_user_id(token: TokenPayload) -> TokenPayload:
    """Look up the internal user UUID for this auth0 sub and attach it.

    Uses a short-TTL in-process cache to avoid a repo round-trip on every
    authed request. The cache is invalidated when a user is deleted.
    """
    cached = _user_id_cache.get(token.sub)
    if cached is not None:
        cached_id, expires_at = cached
        if expires_at > time.time():
            return token.model_copy(update={"id": cached_id})
        # Expired — drop the stale entry.
        _user_id_cache.pop(token.sub, None)

    from app.db.provider import get_user_repo

    try:
        repo = get_user_repo()
    except HTTPException:
        return token
    if repo is None:
        return token
    user = await repo.get_user_by_auth0_id(token.sub)
    if user:
        _user_id_cache[token.sub] = (
            user["id"],
            time.time() + _USER_ID_CACHE_TTL_SEC,
        )
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
    """Require admin: either DB role is admin/super_admin OR the user appears
    in ``settings.ADMIN_USER_IDS``. Fix 4 — until OAuth scopes land, the
    env allow-list is the de-facto gate; the DB-role path stays so seeded
    admins can be promoted from the admin UI without env changes."""
    from app.auth.roles import has_admin_access, user_id_is_admin
    from app.db.provider import get_user_repo

    if user_id_is_admin(user.id, user.sub):
        return user

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


def require_internal_service(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Service-to-service auth gate. Rejects everything except an exact
    match of ``Authorization: Bearer <INTERNAL_SERVICE_TOKEN>``.

    Used by routes that ``lingo-async`` calls back into on behalf of a
    user — e.g. ``/quests/_internal/{id}/progress``. Auth0 JWTs are
    rejected here so a leaked user token can't masquerade as the worker.
    """
    if not settings.INTERNAL_SERVICE_TOKEN:
        raise HTTPException(
            status_code=500, detail="INTERNAL_SERVICE_TOKEN not configured"
        )
    if authorization != f"Bearer {settings.INTERNAL_SERVICE_TOKEN}":
        raise HTTPException(status_code=401, detail="invalid system token")
