"""Dependency-injection wiring for database repositories.

At startup the app calls ``init_repositories()`` which reads ``DB_BACKEND``
from config and instantiates the right concrete class.  FastAPI dependencies
(``get_user_repo``, etc.) return the singleton for the lifetime of the app.

Community uses a separate table. For now, community always uses MockCommunityRepository
until SqliteCommunityRepository / DynamoCommunityRepository are implemented.
"""

from typing import Any

from app.config import settings
from app.db.protocols import UserRepository

_user_repo: UserRepository | None = None
_community_repo: Any = None


async def init_repositories() -> None:
    """Create and connect the repository singletons based on config."""
    global _user_repo

    if settings.DB_BACKEND == "sqlite":
        from app.db.sqlite import SqliteUserRepository

        repo = SqliteUserRepository(settings.SQLITE_PATH)
        await repo.connect()
        _user_repo = repo

    elif settings.DB_BACKEND == "dynamodb":
        from app.db.dynamo import DynamoUserRepository

        table = f"{settings.DYNAMODB_TABLE_PREFIX}users"
        repo = DynamoUserRepository(table, settings.AWS_REGION)
        await repo.connect()
        _user_repo = repo

    else:
        raise ValueError(f"Unknown DB_BACKEND: {settings.DB_BACKEND!r}")

    # Community: always use mock until SQLite/Dynamo implementations are ready
    global _community_repo
    from app.db.mock_community import MockCommunityRepository

    _community_repo = MockCommunityRepository()


async def shutdown_repositories() -> None:
    """Gracefully close all repository connections."""
    if _user_repo and hasattr(_user_repo, "close"):
        await _user_repo.close()  # type: ignore[union-attr]


def get_user_repo() -> UserRepository:
    """FastAPI dependency — returns the active UserRepository instance."""
    assert _user_repo is not None, "repositories not initialised (call init_repositories first)"
    return _user_repo


def get_community_repo() -> Any:
    """FastAPI dep — CommunityRepository (currently MockCommunityRepository)."""
    assert _community_repo is not None, "repos not initialised"
    return _community_repo
