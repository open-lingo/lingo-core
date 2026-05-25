import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db.provider import init_repositories, shutdown_repositories
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.v1.router import v1_router

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
        settings.DEBUG, settings.DB_BACKEND, settings.DEV_USER,
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


@app.middleware("http")
async def access_log(request: Request, call_next) -> Response:  # type: ignore[type-arg]
    start = time.perf_counter()
    response: Response = await call_next(request)
    ms = (time.perf_counter() - start) * 1000

    user = request.headers.get("X-Dev-User", "-")
    logger.info(
        "%s %s %s  → %d  (%.0fms)  user=%s",
        request.client.host if request.client else "-",
        request.method,
        request.url.path,
        response.status_code,
        ms,
        user,
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

app.include_router(v1_router, prefix="/api/core/v1")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
