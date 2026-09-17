import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.db.provider import init_repositories, shutdown_repositories
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.shared.request_id import get_request_id
from app.telemetry.guard import TelemetryGuardMiddleware
from app.v1.router import build_v1_router

logger = logging.getLogger("lingo.access")

# Third-party loggers that should stay quiet unless something breaks.
_QUIET_LOGGERS = ("aiosqlite",)


def _configure_logging() -> None:
    lingo_level = logging.DEBUG if settings.DEBUG else logging.INFO
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    logging.getLogger("lingo").setLevel(lingo_level)
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    _configure_logging()
    startup = logging.getLogger("lingo.startup")
    startup.info(
        "DEBUG=%s  DB_BACKEND=%s  DEV_USER=%s",
        settings.DEBUG,
        settings.DB_BACKEND,
        settings.DEV_USER,
    )
    if settings.DEBUG:
        # Fix 5 — the hard guard in config.py refuses DEBUG=true when
        # AWS_LAMBDA_FUNCTION_NAME is set. The CORS warning here is still
        # useful for non-Lambda misconfigurations (docker, EC2 box, etc.).
        startup.info("Auth bypass ACTIVE — all requests authenticate as DEV_USER")
        for origin in settings.CORS_ORIGINS:
            if "localhost" not in origin and "127.0.0.1" not in origin:
                startup.critical(
                    "DEBUG=true with non-local CORS origin %s — disable DEBUG in production",
                    origin,
                )
    await init_repositories()
    yield
    await shutdown_repositories()


app = FastAPI(
    title="Lingo Core API",
    version="0.1.0",
    lifespan=lifespan,
)


# Response compression. Registered HERE, before the access-log middleware,
# which makes it the INNERMOST layer — deliberate. ``access_log`` is a
# ``BaseHTTPMiddleware``, which re-emits every response as a stream with no
# content-length; gzip layered outside it therefore never sees a length, takes
# its streaming path, and compresses everything including 16-byte bodies,
# making ``minimum_size`` silently inert. Sitting beneath it, gzip sees the real
# Content-Length and honors the floor.
#
# This matters most for the SRS endpoints: ``GET /srs/state`` returns a
# learner's entire card store on every app load — measured ~437 bytes/card, so
# a 6k-card learner is ~2.5 MiB raw against the Lambda Function URL's hard 6 MB
# buffered-response cap. Measured end-to-end through this middleware at 1000
# cards: 0.417 MiB raw -> 0.023 MiB on the wire. Take that ratio as a floor,
# not a promise — the fixture is structurally regular; a fully randomized one
# compresses to ~0.17. Real FSRS data sits between. Either way it turns the
# response from "a sizeable fraction of the cap" into a rounding error.
#
# Mangum interaction, since it is non-obvious: GZipMiddleware leaves
# ``content-type: application/json``, which IS in Mangum's text-mime list, so
# ``handle_base64_response_body`` tries ``body.decode()`` first and only
# base64-encodes on UnicodeDecodeError. That fallback is reliable rather than
# lucky — gzip's magic number puts 0x8b at byte 2, a UTF-8 continuation byte in
# a lead position, so the decode always raises. Pinned by tests/test_compression.py.
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.middleware("http")
async def access_log(request: Request, call_next) -> Response:  # type: ignore[type-arg]
    start = time.perf_counter()
    response: Response = await call_next(request)
    ms = (time.perf_counter() - start) * 1000

    # Fixed 2026-09-17 (lane A3b): this used to read the raw `X-Dev-User`
    # header directly, which is only ever set in DEBUG-bypass dev traffic —
    # every authenticated PROD request logged `user=-`, indistinguishable
    # from an anonymous one. `auth_sub_hash` is stashed on `request.state`
    # by `get_current_user`/`get_current_user_optional`
    # (`app/auth/dependencies.py`) the moment either resolves a token —
    # dev-bypass OR a real Auth0 JWT, both funnel through the same return
    # point — so by the time `call_next` above returns, it reflects
    # whichever happened on THIS request. Stays "-" when no auth dependency
    # ran at all (a public route) or the token was missing/invalid. See
    # `log_safe_user_hash`'s docstring for why this is a hash, not the raw
    # `sub`.
    user = getattr(request.state, "auth_sub_hash", None) or "-"
    # `X-Lingo-Platform` (2026-09-17, lane A3b): `src/shared/api/client.ts`
    # now stamps ios/android/web on every request (cheap, no PII) — lets
    # this line distinguish devices for the same account, e.g. Spencer's
    # phone vs. his iPad, without any new identity being logged.
    platform = request.headers.get("X-Lingo-Platform", "-")
    logger.info(
        "%s %s %s  → %d  (%.0fms)  user=%s  platform=%s",
        request.client.host if request.client else "-",
        request.method,
        request.url.path,
        response.status_code,
        ms,
        user,
        platform,
    )
    return response


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)
# Body-size cap + per-IP token bucket, scoped to POST
# /api/core/v1/telemetry/errors only — see app/telemetry/guard.py. Must be
# a BaseHTTPMiddleware (not a route Depends) to reject an oversized body on
# Content-Length alone, before FastAPI reads it into memory.
app.add_middleware(TelemetryGuardMiddleware)

# Built fresh (reads settings.SURFACE_MODE) so the conftest app-reload picks up
# a test-set mode. "beta" mounts only the core loop; "full" (default) is
# unchanged.
app.include_router(build_v1_router(), prefix="/api/core/v1")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ── Exception handlers: echo X-Request-Id on every error response ──────────
#
# A client error report can carry `lastRequestId` (the X-Request-Id from
# whatever API call most recently failed) so a human can grep this
# request's CloudWatch log stream directly instead of correlating by
# timestamp. Before this, no response — success or error — carried the id
# at all. Three handlers cover the app's actual error surface today:
# deliberate `HTTPException` raises (including `api_error`'s wrapped
# 500s), FastAPI's own request-validation 422s, and truly unhandled
# exceptions that would otherwise reach Starlette's default 500 page.
#
# Registering a catch-all `Exception` handler changes existing behavior in
# one place worth flagging: `TestClient(app)` normally RE-RAISES unhandled
# exceptions in tests (`raise_server_exceptions=True` by default) so a bug
# fails the test loudly. An app-level `Exception` handler intercepts
# before that re-raise, so a test that previously asserted a raised
# exception via `pytest.raises(...)` around a route call would instead see
# a 500 JSON response. `tests/test_api_error.py` builds its OWN minimal
# `FastAPI()` app per test (not this module's `app`), so it is unaffected;
# no other test in this repo drives an unhandled exception through the
# real `app.main.app` today (`test_smoke.py` verified clean).


@app.exception_handler(StarletteHTTPException)
async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    request_id = get_request_id(request)
    headers = dict(exc.headers or {})
    headers["X-Request-Id"] = request_id
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=headers)


@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    request_id = get_request_id(request)
    # Mirrors FastAPI's own default handler body shape exactly
    # (`fastapi.exception_handlers.request_validation_exception_handler`) —
    # only the header is new.
    return JSONResponse(
        {"detail": jsonable_encoder(exc.errors())},
        status_code=422,
        headers={"X-Request-Id": request_id},
    )


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = get_request_id(request)
    logger.exception("unhandled_exception request_id=%s path=%s", request_id, request.url.path)
    return JSONResponse(
        {"detail": "Internal server error"},
        status_code=500,
        headers={"X-Request-Id": request_id},
    )
