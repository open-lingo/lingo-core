"""FastAPI dependency that validates an Auth0 JWT and returns the token payload.

After JWT validation the auth0 ``sub`` is resolved to our internal user UUID
via the UserRepository.  All domain code uses ``user.id`` (the UUID); only
auth-specific code (registration, JWT validation) touches ``user.sub``.

In DEBUG mode, JWT validation is skipped entirely.  The user identity is
resolved from (in order):

1. ``X-Dev-User`` header  (override to impersonate any seeded user)
2. ``DEV_USER`` env var   (default dev identity)
"""

import hashlib
import logging
import secrets
import time
import uuid
from typing import Annotated, Any

import httpx
from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.auth.schemas import TokenPayload
from app.config import settings
from app.db.protocols.user import UserAlreadyExistsError

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


def log_safe_user_hash(sub: str) -> str:
    """Stable, non-PII stand-in for an Auth0 `sub` in ACCESS logs.

    `app.main`'s `lingo.access` line (`user=%s`) used to print `-` for
    every request, authenticated ones included — it was only ever reading
    the raw `X-Dev-User` header, which real (non-DEBUG) traffic never
    sets. Fixed 2026-09-17 (lane A3b) by stashing this hash on
    `request.state.auth_sub_hash` at the bottom of `get_current_user` /
    `get_current_user_optional` (below), which the access-log middleware
    reads after `call_next` returns.

    Hash, not the raw `sub`, by design: a `sub` (`auth0|...`,
    `google-oauth2|...`) is a stable external account identifier, and this
    codebase already treats a raw account/user id as too identifying to
    put in a log line for telemetry purposes (see the client-error/
    diagnostics endpoints' explicit no-PII schema, `app/telemetry/
    schemas.py`) — even though `lingo.access` is a separate, older,
    internal-only logger. sha256 truncated to 8 hex chars is STABLE (same
    sub -> same hash, every request, forever — unlike a random per-session
    id, that's the whole point: it lets Spencer tell "same account,
    different request" apart, e.g. his phone session from his iPad
    session, by matching hashes across `lingo.access` lines) and NOT
    reversible from the hash alone (only by testing candidate subs against
    it, not a concern for an internal access log's threat model).
    """
    return hashlib.sha256(sub.encode("utf-8")).hexdigest()[:8]


# Header the client stamps on every authenticated request with the
# device's IANA zone (`Intl.DateTimeFormat().resolvedOptions().timeZone`,
# `src/shared/api/client.ts`). See `sync_request_timezone` below and
# `app/shared/timezone.py` for validation.
_LINGO_TZ_HEADER = "X-Lingo-Timezone"


async def sync_request_timezone(
    request: Request,
    repo: Any,
    user_id: str,
    record: dict[str, Any] | None,
) -> None:
    """Best-effort: keep the user row's ``timezone`` attribute in sync with
    the caller's device zone.

    Last-write-wins: whichever device's request lands last is the zone
    ``app/quests/router.py`` uses to bucket that user's daily/weekly quest
    resets by LOCAL calendar day — see the quest-timezone memory's design.

    Called from ``GET /boot`` (``app/boot/router.py::get_boot``), not from
    every authenticated dependency resolution — the client sends
    ``X-Lingo-Timezone`` on every request (see ``src/shared/api/
    client.ts``), but boot already fires once per session/app-open and
    every other per-session sync-ish thing (progress touch, srs state)
    already batches there. Hooking this into ``get_registered_user``
    instead was tried first and reverted: it added a conditional
    ``update_user`` call to EVERY authenticated route, including
    write-heavy hot paths like the lesson batch submit, which broke
    ``tests/test_progress.py::test_batch_collapses_to_one_user_update``'s
    "exactly one update_user call" cost guarantee for no real freshness
    benefit (a device's zone doesn't change mid-batch).

    ``record`` is a read the caller already performed for another reason
    (in ``get_boot``, the same read ``get_me`` needs), so comparing
    against it costs nothing extra; this function only ever adds a
    WRITE, and only when the header disagrees with what's already stored
    (a garbage/missing header validates down to "UTC", so a user who's
    never sent a real zone converges to UTC once and then never writes
    again until it changes). Never raises — a timezone header must never
    be the reason an otherwise-valid request fails.
    """
    if repo is None or record is None:
        return
    from app.shared.timezone import validate_timezone_name

    tz_name = validate_timezone_name(request.headers.get(_LINGO_TZ_HEADER))
    if record.get("timezone") == tz_name:
        return
    try:
        await repo.update_user(user_id, {"timezone": tz_name}, current=record)
    except Exception:  # noqa: BLE001 — best-effort, never blocks the request
        logger.warning("timezone_sync_failed user_id=%s", user_id)


def _stash_auth_sub_hash(request: Request, sub: str) -> None:
    """Best-effort: a request object that can't take new state attributes
    (shouldn't happen with Starlette's Request, but this must never be the
    reason an otherwise-valid auth resolution fails) is swallowed."""
    try:
        request.state.auth_sub_hash = log_safe_user_hash(sub)
    except Exception:  # noqa: BLE001
        pass


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

    resolved = await _resolve_user_id(token)
    _stash_auth_sub_hash(request, resolved.sub)
    return resolved


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

    resolved = await _resolve_user_id(token)
    _stash_auth_sub_hash(request, resolved.sub)
    return resolved


def _provisional_user_id(auth0_id: str) -> str:
    """Deterministic id for a not-yet-registered identity's placeholder row.

    Derived (uuid5, not random) so two requests racing to provision the
    SAME auth0_id — e.g. `GET /boot` and `GET /users/me` firing within a
    few ms of each other on first login, exactly the FIRSTRUN storm —
    compute the identical id and collapse to one row via
    `UserAlreadyExistsError` instead of each minting a distinct uuid4 and
    both winning their own create.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"lingo-provisional-user:{auth0_id}"))


def _provisional_username(auth0_id: str) -> str:
    return f"user_{hashlib.sha256(auth0_id.encode('utf-8')).hexdigest()[:12]}"


async def _provision_user(repo: Any, auth0_id: str) -> dict[str, Any]:
    """Idempotently create (or fetch) a placeholder row for a first-touch,
    not-yet-registered Auth0 identity (FIRSTRUN lane, 2026-09-18).

    Why: every route behind `get_registered_user` — `GET /boot`,
    `GET /users/me`, `/users/me/settings`, `/progress/me*`, `/srs/state`,
    `/quests`, `/decks/admin`, `/users/discover`, `/social/profiles/*` —
    used to 404 for a brand-new signup until the client's separate
    `POST /users/me` registration form was submitted. On first login the
    client's boot wave fires 8+ of these concurrently, so a real user saw
    ~26 s and 15+ 404s before the app worked (see the lane's evidence).
    Provisioning here removes the 404 entirely; client-side ordering can
    no longer produce the storm because there is no failure state left to
    race.

    The row is intentionally sparse: `display_name == ""` is the sentinel
    `register_user` (`POST /users/me`) and the client both use to
    recognize "provisioned but not yet registered" — `UserCreate` and
    `MeUpdate` both require `min_length=1` on `display_name`, so no real
    registration can ever produce that value, and it costs no schema
    change (no new column) on either backend. `register_user` claims this
    row in place (same id, real username + display_name) instead of
    409ing "already registered".

    No XP/quest side effects: this only inserts the user row itself —
    `create_user` does not touch progress, SRS, or quest tables.
    """
    user_id = _provisional_user_id(auth0_id)
    try:
        return await repo.create_user(
            {
                "id": user_id,
                "auth0_id": auth0_id,
                "username": _provisional_username(auth0_id),
                "display_name": "",
            }
        )
    except UserAlreadyExistsError:
        # Lost the race (or a retry after our own earlier success) —
        # someone else's create for this same deterministic id landed
        # first. Re-fetch rather than treat this as a real error.
        existing = await repo.get_user_by_id(user_id)
        if existing is not None:
            return existing
        raise


async def get_registered_user(
    user: Annotated[TokenPayload, Depends(get_current_user)],
) -> TokenPayload:
    """Like get_current_user but auto-provisions a placeholder row for a
    not-yet-registered (but authenticated) identity instead of 404ing —
    see `_provision_user`. Also blocks banned users with 403 USER_BANNED.
    """
    from app.auth.ban import raise_if_user_banned
    from app.db.provider import get_user_repo

    repo = get_user_repo()
    record: dict[str, Any] | None = None
    if user.id is None:
        if repo is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                "User not registered — complete registration first",
            )
        record = await _provision_user(repo, user.sub)
        # Refresh the short-TTL sub->id cache immediately so the REST of
        # this same boot wave (and any other warm container that already
        # cached the miss) doesn't re-provision or re-404 before the TTL
        # would naturally have picked up the new row.
        _user_id_cache[user.sub] = (record["id"], time.time() + _USER_ID_CACHE_TTL_SEC)
        user = user.model_copy(update={"id": record["id"]})
    elif repo:
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


async def get_acting_user(
    request: Request,
    user: Annotated[TokenPayload, Depends(get_registered_user)],
) -> TokenPayload:
    """Like ``get_registered_user`` but honors ``X-Impersonate-User-Id``.

    When an admin sets that header on a request, the dependency swaps the
    returned ``TokenPayload``'s ``id`` and ``sub`` for the target user's
    (preserving the admin originals as ``actor_id`` / ``actor_sub``). The
    rest of the request is then processed as if the target user made it:
    user.xp credits land on the target, /users/me reflects the target,
    etc. Every impersonated request is audit-logged with the admin as
    actor.

    Trust gate:
      1. Caller must be admin (per ``require_admin``'s rules — env
         allow-list OR DB role).
      2. Target user must exist.
      3. The admin's own ``id`` is never swapped if it equals the
         target (no-op self-impersonation just passes through).

    Returns the (possibly swapped) ``TokenPayload``. If the header is
    absent the JWT user is returned unchanged; for non-admins the header
    is silently ignored (a leaked header on a regular user's tab must
    not lock them out).

    Routes that need to ALWAYS resolve to the JWT identity (e.g.
    /users/me/settings, account deletion, payment) should keep using
    ``get_registered_user`` directly so admins can't accidentally mutate
    sensitive settings while acting-as.
    """
    target_id = request.headers.get("X-Impersonate-User-Id")
    if not target_id:
        return user

    # Cheap-bail: self-impersonation is a no-op.
    if target_id == user.id:
        return user

    # Admin gate — reuse require_admin's logic without re-raising 403 (we
    # want to silently ignore the header for non-admins; otherwise a
    # leaked header could DoS regular users with 403s).
    from app.auth.roles import has_admin_access, user_id_is_admin
    from app.db.provider import get_user_repo

    is_admin = user_id_is_admin(user.id, user.sub)
    repo = get_user_repo()
    if not is_admin and repo is not None and user.id is not None:
        record = await repo.get_user_by_id(user.id)
        if record and has_admin_access(record.get("role") or "user"):
            is_admin = True
    if not is_admin:
        logger.warning(
            "X-Impersonate-User-Id ignored: caller not admin (sub=%s target=%s)",
            user.sub,
            target_id,
        )
        return user

    # Resolve target. Missing target is a hard 404 — admin should fix
    # their request rather than silently get the wrong user.
    if repo is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "user repo unavailable"
        )
    target = await repo.get_user_by_id(target_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "impersonation target not found")

    # Best-effort audit log per impersonated request. Failure swallowed so
    # a broken audit log can't break the impersonation path.
    try:
        from app.admin.audit_router import record_admin_action

        await record_admin_action(
            actor_id=user.id,
            action="impersonate_request",
            target_id=target_id,
            target_kind="user",
            payload={
                "method": request.method,
                "path": request.url.path,
                "actor_sub": user.sub,
            },
        )
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("impersonate_request audit failed: %s", exc)

    return user.model_copy(
        update={
            "id": target["id"],
            "sub": target.get("auth0_id") or user.sub,
            "actor_id": user.id,
            "actor_sub": user.sub,
        }
    )


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

    The token is resolved via ``resolve_internal_service_token`` (env wins;
    SSM ``/lingo/internal-service-token`` is the empty-env prod fallback).
    The resolver re-reads ``settings`` per call so the conftest module-reload
    pattern (which replaces the ``app.config.settings`` singleton) still
    picks up test-time monkeypatches.
    """
    from app.auth.internal_token import resolve_internal_service_token

    token = resolve_internal_service_token()
    if not token:
        raise HTTPException(
            status_code=500, detail="INTERNAL_SERVICE_TOKEN not configured"
        )
    # Constant-time compare so the internal token can't be recovered byte-by-byte
    # via response-timing on the public Function URL. compare_digest needs equal
    # lengths to be fully constant-time, but its early-out still beats ``!=``.
    if not secrets.compare_digest(authorization or "", f"Bearer {token}"):
        raise HTTPException(status_code=401, detail="invalid system token")
