import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.community.router import router as community_router
from app.decks.router import router as decks_router
from app.config import settings
from app.db.dependencies import init_repositories, shutdown_repositories
from app.srs.router import router as srs_router
from app.users.router import router as users_router

logger = logging.getLogger("lingo.access")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logging.basicConfig(
        level=logging.DEBUG if settings.DEBUG else logging.INFO,
        format="%(asctime)s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    startup = logging.getLogger("lingo.startup")
    startup.info(
        "DEBUG=%s  DB_BACKEND=%s  DEV_USER=%s",
        settings.DEBUG, settings.DB_BACKEND, settings.DEV_USER,
    )
    if settings.DEBUG:
        startup.info("Auth bypass ACTIVE — all requests authenticate as DEV_USER")
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


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(users_router)
app.include_router(srs_router)
app.include_router(community_router)
app.include_router(decks_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
