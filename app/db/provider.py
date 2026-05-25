"""Database provider — wires concrete repository implementations for FastAPI DI.

At startup the app calls ``init_repositories()`` which reads ``DB_BACKEND``
from config and instantiates the right concrete class.  FastAPI dependencies
(``get_user_repo``, etc.) return the singleton for the lifetime of the app.

Community uses a separate table; it always uses MockCommunityRepository
until SqliteCommunityRepository / DynamoCommunityRepository are promoted.

Fix 6 — repository init is fault-tolerant. If a connect() raises we log
the failure, record the domain in ``_degraded`` and keep going. Routers
that depend on a degraded repo return 503 via the ``get_*_repo`` accessors.
"""

import logging
from typing import Any

from app.config import settings
from app.db.protocols import (
    DeckRepository,
    ProgressRepository,
    SocialRepository,
    SRSRepository,
    StoryRepository,
    SubscriptionRepository,
    UserRepository,
)

logger = logging.getLogger("lingo.startup")

_user_repo: UserRepository | None = None
_community_repo: Any = None
_srs_repo: SRSRepository | None = None
_deck_repo: DeckRepository | None = None
_subscription_repo: SubscriptionRepository | None = None
_story_repo: StoryRepository | None = None
_progress_repo: ProgressRepository | None = None
_social_repo: SocialRepository | None = None

# Set of domain names whose connect() raised during startup.
_degraded: set[str] = set()


async def _safe_connect(domain: str, repo: Any) -> Any | None:
    """Call ``repo.connect()`` and return the repo on success.

    On failure, log + record the domain in ``_degraded`` and return None.
    """
    try:
        await repo.connect()
        return repo
    except Exception as exc:  # noqa: BLE001 — we want every failure caught
        _degraded.add(domain)
        logger.error("Repository init failed for %s: %s", domain, exc)
        return None


async def init_repositories() -> None:
    """Create and connect the repository singletons based on config."""
    global _user_repo, _srs_repo, _deck_repo, _subscription_repo, _story_repo, _progress_repo
    global _social_repo

    _degraded.clear()

    if settings.DB_BACKEND == "sqlite":
        from app.db.sqlite.deck import SqliteDeckRepository
        from app.db.sqlite.progress import SqliteProgressRepository
        from app.db.sqlite.social import SqliteSocialRepository
        from app.db.sqlite.srs import SqliteSRSRepository
        from app.db.sqlite.story import SqliteStoryRepository
        from app.db.sqlite.subscription import SqliteSubscriptionRepository
        from app.db.sqlite.user import SqliteUserRepository

        _user_repo = await _safe_connect("user", SqliteUserRepository(settings.SQLITE_PATH))
        _srs_repo = await _safe_connect("srs", SqliteSRSRepository(settings.SQLITE_PATH))
        _deck_repo = await _safe_connect("deck", SqliteDeckRepository(settings.SQLITE_PATH))
        _subscription_repo = await _safe_connect(
            "subscription", SqliteSubscriptionRepository(settings.SQLITE_PATH)
        )
        _story_repo = await _safe_connect("story", SqliteStoryRepository(settings.SQLITE_PATH))
        _progress_repo = await _safe_connect(
            "progress", SqliteProgressRepository(settings.SQLITE_PATH)
        )
        _social_repo = await _safe_connect("social", SqliteSocialRepository(settings.SQLITE_PATH))

    elif settings.DB_BACKEND == "dynamodb":
        from app.db.dynamo.deck import DynamoDeckRepository
        from app.db.dynamo.progress import DynamoProgressRepository
        from app.db.dynamo.social import DynamoSocialRepository
        from app.db.dynamo.srs import DynamoSRSRepository
        from app.db.dynamo.subscription import DynamoSubscriptionRepository
        from app.db.dynamo.user import DynamoUserRepository

        prefix = settings.DYNAMODB_TABLE_PREFIX
        region = settings.AWS_REGION

        _user_repo = await _safe_connect("user", DynamoUserRepository(f"{prefix}users", region))
        _srs_repo = await _safe_connect("srs", DynamoSRSRepository(f"{prefix}srs", region))
        _deck_repo = await _safe_connect("deck", DynamoDeckRepository(f"{prefix}decks", region))
        _subscription_repo = await _safe_connect(
            "subscription", DynamoSubscriptionRepository(f"{prefix}subscriptions", region)
        )

        _story_repo = None  # Stories not yet supported for DynamoDB

        _progress_repo = await _safe_connect(
            "progress", DynamoProgressRepository(f"{prefix}progress", region)
        )

        # SQLite-first per maintainer instruction 2026-05-25 — the Dynamo
        # social repo is a stub that raises NotImplementedError on use.
        _social_repo = await _safe_connect(
            "social", DynamoSocialRepository(f"{prefix}social", region)
        )

    else:
        raise ValueError(f"Unknown DB_BACKEND: {settings.DB_BACKEND!r}")

    global _community_repo
    from app.db.mock.community import MockCommunityRepository

    _community_repo = MockCommunityRepository()

    if _degraded:
        logger.warning("Provider booted in degraded mode: %s", sorted(_degraded))


async def shutdown_repositories() -> None:
    """Gracefully close all repository connections."""
    if _user_repo and hasattr(_user_repo, "close"):
        await _user_repo.close()  # type: ignore[union-attr]
    if _srs_repo and hasattr(_srs_repo, "close"):
        await _srs_repo.close()  # type: ignore[union-attr]
    if _deck_repo and hasattr(_deck_repo, "close"):
        await _deck_repo.close()
    if _subscription_repo and hasattr(_subscription_repo, "close"):
        await _subscription_repo.close()  # type: ignore[union-attr]
    if _story_repo and hasattr(_story_repo, "close"):
        await _story_repo.close()  # type: ignore[union-attr]
    if _progress_repo and hasattr(_progress_repo, "close"):
        await _progress_repo.close()  # type: ignore[union-attr]
    if _social_repo and hasattr(_social_repo, "close"):
        await _social_repo.close()  # type: ignore[union-attr]


def _raise_degraded(domain: str) -> None:
    """Raise 503 for an unavailable domain."""
    from fastapi import HTTPException, status

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"{domain} storage is unavailable",
    )


def get_user_repo() -> UserRepository:
    if _user_repo is None:
        _raise_degraded("user")
    return _user_repo  # type: ignore[return-value]


def get_srs_repo() -> SRSRepository:
    if _srs_repo is None:
        _raise_degraded("srs")
    return _srs_repo  # type: ignore[return-value]


def get_community_repo() -> Any:
    assert _community_repo is not None, "repositories not initialised (call init_repositories first)"
    return _community_repo


def get_deck_repo() -> DeckRepository | None:
    return _deck_repo


def get_subscription_repo() -> SubscriptionRepository | None:
    return _subscription_repo


def get_story_repo() -> StoryRepository | None:
    return _story_repo


def get_progress_repo() -> ProgressRepository:
    if _progress_repo is None:
        _raise_degraded("progress")
    return _progress_repo  # type: ignore[return-value]


def get_social_repo() -> SocialRepository | None:
    return _social_repo


def degraded_domains() -> set[str]:
    """Read-only view for /health and debugging."""
    return set(_degraded)
