"""Dependency-injection wiring for database repositories.

At startup the app calls ``init_repositories()`` which reads ``DB_BACKEND``
from config and instantiates the right concrete class.  FastAPI dependencies
(``get_user_repo``, etc.) return the singleton for the lifetime of the app.

Community uses a separate table. For now, community always uses MockCommunityRepository
until SqliteCommunityRepository / DynamoCommunityRepository are implemented.
"""

from typing import Any

from app.config import settings
from app.db.protocols import (
    DeckRepository,
    SRSRepository,
    SubscriptionRepository,
    UserRepository,
)

_user_repo: UserRepository | None = None
_community_repo: Any = None
_srs_repo: SRSRepository | None = None
_deck_repo: DeckRepository | None = None
_subscription_repo: SubscriptionRepository | None = None


async def init_repositories() -> None:
    """Create and connect the repository singletons based on config."""
    global _user_repo, _srs_repo, _deck_repo, _subscription_repo

    if settings.DB_BACKEND == "sqlite":
        from app.db.sqlite import SqliteUserRepository

        repo = SqliteUserRepository(settings.SQLITE_PATH)
        await repo.connect()
        _user_repo = repo

        from app.db.srs_sqlite import SqliteSRSRepository

        srs = SqliteSRSRepository(settings.SQLITE_PATH)
        await srs.connect()
        _srs_repo = srs

        from app.db.deck_sqlite import SqliteDeckRepository

        deck = SqliteDeckRepository(settings.SQLITE_PATH)
        await deck.connect()
        _deck_repo = deck

        from app.db.subscription_sqlite import SqliteSubscriptionRepository

        sub = SqliteSubscriptionRepository(settings.SQLITE_PATH)
        await sub.connect()
        _subscription_repo = sub

    elif settings.DB_BACKEND == "dynamodb":
        from app.db.dynamo import DynamoUserRepository

        table = f"{settings.DYNAMODB_TABLE_PREFIX}users"
        repo = DynamoUserRepository(table, settings.AWS_REGION)
        await repo.connect()
        _user_repo = repo

        # TODO: DynamoSRSRepository once implemented
        raise NotImplementedError("DynamoDB SRS repository not yet implemented")

    else:
        raise ValueError(f"Unknown DB_BACKEND: {settings.DB_BACKEND!r}")

    # Deck + Community: deck uses Sqlite when sqlite; community always mock
    if settings.DB_BACKEND != "sqlite":
        _deck_repo = None
        _subscription_repo = None

    # Community: always use mock until SQLite/Dynamo implementations are ready
    global _community_repo
    from app.db.mock_community import MockCommunityRepository

    _community_repo = MockCommunityRepository()


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


def get_user_repo() -> UserRepository:
    """FastAPI dependency — returns the active UserRepository instance."""
    assert _user_repo is not None, "repositories not initialised (call init_repositories first)"
    return _user_repo


def get_srs_repo() -> SRSRepository:
    """FastAPI dependency — returns the active SRSRepository instance."""
    assert _srs_repo is not None, "repositories not initialised (call init_repositories first)"
    return _srs_repo


def get_community_repo() -> Any:
    """FastAPI dep — CommunityRepository (currently MockCommunityRepository)."""
    assert _community_repo is not None, "repos not initialised"
    return _community_repo


def get_deck_repo() -> DeckRepository | None:
    """FastAPI dep — DeckRepository (Sqlite when DB_BACKEND=sqlite, else None)."""
    return _deck_repo


def get_subscription_repo() -> SubscriptionRepository | None:
    """FastAPI dep — SubscriptionRepository (Sqlite when DB_BACKEND=sqlite, else None)."""
    return _subscription_repo
