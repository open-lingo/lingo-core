"""Database provider — wires concrete repository implementations for FastAPI DI.

At startup the app calls ``init_repositories()`` which reads ``DB_BACKEND``
from config and instantiates the right concrete class.  FastAPI dependencies
(``get_user_repo``, etc.) return the singleton for the lifetime of the app.

Community uses a separate table; it always uses MockCommunityRepository
until SqliteCommunityRepository / DynamoCommunityRepository are promoted.
"""

from typing import Any

from app.config import settings
from app.db.protocols import (
    DeckRepository,
    SRSRepository,
    StoryRepository,
    SubscriptionRepository,
    UserRepository,
)

_user_repo: UserRepository | None = None
_community_repo: Any = None
_srs_repo: SRSRepository | None = None
_deck_repo: DeckRepository | None = None
_subscription_repo: SubscriptionRepository | None = None
_story_repo: StoryRepository | None = None


async def init_repositories() -> None:
    """Create and connect the repository singletons based on config."""
    global _user_repo, _srs_repo, _deck_repo, _subscription_repo, _story_repo

    if settings.DB_BACKEND == "sqlite":
        from app.db.sqlite.user import SqliteUserRepository

        repo = SqliteUserRepository(settings.SQLITE_PATH)
        await repo.connect()
        _user_repo = repo

        from app.db.sqlite.srs import SqliteSRSRepository

        srs = SqliteSRSRepository(settings.SQLITE_PATH)
        await srs.connect()
        _srs_repo = srs

        from app.db.sqlite.deck import SqliteDeckRepository

        deck = SqliteDeckRepository(settings.SQLITE_PATH)
        await deck.connect()
        _deck_repo = deck

        from app.db.sqlite.subscription import SqliteSubscriptionRepository

        sub = SqliteSubscriptionRepository(settings.SQLITE_PATH)
        await sub.connect()
        _subscription_repo = sub

        from app.db.sqlite.story import SqliteStoryRepository

        story_repo = SqliteStoryRepository(settings.SQLITE_PATH)
        await story_repo.connect()
        _story_repo = story_repo

    elif settings.DB_BACKEND == "dynamodb":
        from app.db.dynamo.user import DynamoUserRepository
        from app.db.dynamo.srs import DynamoSRSRepository
        from app.db.dynamo.deck import DynamoDeckRepository
        from app.db.dynamo.subscription import DynamoSubscriptionRepository

        prefix = settings.DYNAMODB_TABLE_PREFIX
        region = settings.AWS_REGION

        repo = DynamoUserRepository(f"{prefix}users", region)
        await repo.connect()
        _user_repo = repo

        srs = DynamoSRSRepository(f"{prefix}srs", region)
        await srs.connect()
        _srs_repo = srs

        deck = DynamoDeckRepository(f"{prefix}decks", region)
        await deck.connect()
        _deck_repo = deck

        sub = DynamoSubscriptionRepository(f"{prefix}subscriptions", region)
        await sub.connect()
        _subscription_repo = sub

        _story_repo = None  # Stories not yet supported for DynamoDB

    else:
        raise ValueError(f"Unknown DB_BACKEND: {settings.DB_BACKEND!r}")

    global _community_repo
    from app.db.mock.community import MockCommunityRepository

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
    if _story_repo and hasattr(_story_repo, "close"):
        await _story_repo.close()  # type: ignore[union-attr]


def get_user_repo() -> UserRepository:
    assert _user_repo is not None, "repositories not initialised (call init_repositories first)"
    return _user_repo


def get_srs_repo() -> SRSRepository:
    assert _srs_repo is not None, "repositories not initialised (call init_repositories first)"
    return _srs_repo


def get_community_repo() -> Any:
    assert _community_repo is not None, "repositories not initialised (call init_repositories first)"
    return _community_repo


def get_deck_repo() -> DeckRepository | None:
    return _deck_repo


def get_subscription_repo() -> SubscriptionRepository | None:
    return _subscription_repo


def get_story_repo() -> StoryRepository | None:
    return _story_repo
