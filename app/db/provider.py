"""Database provider — wires concrete repository implementations for FastAPI DI.

At startup the app calls ``init_repositories()`` which reads ``DB_BACKEND``
from config and instantiates the right concrete class.  FastAPI dependencies
(``get_user_repo``, etc.) return the singleton for the lifetime of the app.

Community uses a separate table. ``SqliteCommunityRepository`` is the real
SQLite-backed impl. Dynamo doesn't have a real impl yet — the stub raises on
every method, so for ``DB_BACKEND=dynamodb`` we fall back to
``MockCommunityRepository`` so the routers stay alive while we wait for the
prod impl. (The mock is in-memory and resets every Lambda cold start — that's
documented in ARCHITECTURE_REVIEW.md.)

Fix 6 — repository init is fault-tolerant. If a connect() raises we log
the failure, record the domain in ``_degraded`` and keep going. Routers
that depend on a degraded repo return 503 via the ``get_*_repo`` accessors.
"""

import logging
from typing import Any

from app.config import settings
from app.db.protocols import (
    AuditRepository,
    CommunityRepository,
    DeckRepository,
    PlatformSettingsRepository,
    ProgressRepository,
    QuestRepository,
    SocialRepository,
    SRSRepository,
    StoryRepository,
    SubscriptionRepository,
    TagRepository,
    UserRepository,
)

logger = logging.getLogger("lingo.startup")

_user_repo: UserRepository | None = None
_community_repo: CommunityRepository | None = None
_srs_repo: SRSRepository | None = None
_deck_repo: DeckRepository | None = None
_subscription_repo: SubscriptionRepository | None = None
_story_repo: StoryRepository | None = None
_progress_repo: ProgressRepository | None = None
_social_repo: SocialRepository | None = None
_quest_repo: QuestRepository | None = None
_platform_settings_repo: PlatformSettingsRepository | None = None
_tag_repo: TagRepository | None = None
_audit_repo: AuditRepository | None = None

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
    global _social_repo, _quest_repo, _platform_settings_repo, _tag_repo, _audit_repo

    _degraded.clear()

    if settings.DB_BACKEND == "sqlite":
        from app.db.sqlite.deck import SqliteDeckRepository
        from app.db.sqlite.platform_settings import SqlitePlatformSettingsRepository
        from app.db.sqlite.progress import SqliteProgressRepository
        from app.db.sqlite.quests import SqliteQuestRepository
        from app.db.sqlite.social import SqliteSocialRepository
        from app.db.sqlite.srs import SqliteSRSRepository
        from app.db.sqlite.story import SqliteStoryRepository
        from app.db.sqlite.subscription import SqliteSubscriptionRepository
        from app.db.sqlite.tag import SqliteTagRepository
        from app.db.sqlite.user import SqliteUserRepository

        _user_repo = await _safe_connect("user", SqliteUserRepository(settings.SQLITE_PATH))
        _srs_repo = await _safe_connect("srs", SqliteSRSRepository(settings.SQLITE_PATH))
        _deck_repo = await _safe_connect("deck", SqliteDeckRepository(settings.SQLITE_PATH))
        _subscription_repo = await _safe_connect("subscription", SqliteSubscriptionRepository(settings.SQLITE_PATH))
        _story_repo = await _safe_connect("story", SqliteStoryRepository(settings.SQLITE_PATH))
        _progress_repo = await _safe_connect("progress", SqliteProgressRepository(settings.SQLITE_PATH))
        _social_repo = await _safe_connect("social", SqliteSocialRepository(settings.SQLITE_PATH))
        _quest_repo = await _safe_connect("quest", SqliteQuestRepository(settings.SQLITE_PATH))
        _platform_settings_repo = await _safe_connect(
            "platform_settings",
            SqlitePlatformSettingsRepository(settings.SQLITE_PATH),
        )
        _tag_repo = await _safe_connect("tag", SqliteTagRepository(settings.SQLITE_PATH))

        from app.db.sqlite.audit import SqliteAuditRepository

        _audit_repo = await _safe_connect("audit", SqliteAuditRepository(settings.SQLITE_PATH))

    elif settings.DB_BACKEND == "dynamodb":
        from app.db.dynamo.deck import DynamoDeckRepository
        from app.db.dynamo.progress import DynamoProgressRepository
        from app.db.dynamo.quests import DynamoQuestRepository
        from app.db.dynamo.social import DynamoSocialRepository
        from app.db.dynamo.srs import DynamoSRSRepository
        from app.db.dynamo.subscription import DynamoSubscriptionRepository
        from app.db.dynamo.user import DynamoUserRepository

        prefix = settings.DYNAMODB_TABLE_PREFIX
        region = settings.AWS_REGION

        _user_repo = await _safe_connect("user", DynamoUserRepository(f"{prefix}users", region))
        _srs_repo = await _safe_connect("srs", DynamoSRSRepository(f"{prefix}srs", region))
        _deck_repo = await _safe_connect("deck", DynamoDeckRepository(f"{prefix}decks", region))
        _subscription_repo = await _safe_connect("subscription", DynamoSubscriptionRepository(f"{prefix}subscriptions", region))

        # Stories — no Dynamo impl yet; fall back to the in-memory mock so
        # admin moderation + user browse don't 503. Data is ephemeral per
        # Lambda container (same trade-off MockCommunityRepository takes).
        from app.db.mock.story import MockStoryRepository

        _story_repo = MockStoryRepository()

        _progress_repo = await _safe_connect("progress", DynamoProgressRepository(f"{prefix}progress", region))

        # SQLite-first per maintainer instruction 2026-05-25 — the Dynamo
        # social repo is a stub that raises NotImplementedError on use.
        _social_repo = await _safe_connect("social", DynamoSocialRepository(f"{prefix}social", region))

        # Quests — Dynamo stub still raises for now, but we override it with
        # an in-memory mock so /quests endpoints return real (ephemeral) data
        # instead of relying on the inert read-stubs. Drops on cold start.
        from app.db.mock.quests import MockQuestRepository

        _quest_repo = MockQuestRepository()

        # Platform settings — same in-memory fallback; admin can read/write
        # ephemeral settings until the durable Dynamo impl lands.
        from app.db.mock.platform_settings import MockPlatformSettingsRepository

        _platform_settings_repo = MockPlatformSettingsRepository()

        # Tags — SQLite-first; Dynamo stub raises on use until the cut-over.
        from app.db.dynamo.tag import DynamoTagRepository

        _tag_repo = await _safe_connect("tag", DynamoTagRepository(f"{prefix}tags", region))

        # Audit — SQLite-first per established pattern. Dynamo impl is a
        # stub that raises on append/list until the production cut-over.
        from app.db.dynamo.audit import DynamoAuditRepository

        _audit_repo = await _safe_connect("audit", DynamoAuditRepository(f"{prefix}admin_audit", region))

    else:
        raise ValueError(f"Unknown DB_BACKEND: {settings.DB_BACKEND!r}")

    global _community_repo
    if settings.DB_BACKEND == "sqlite":
        from app.db.sqlite.community import SqliteCommunityRepository

        _community_repo = await _safe_connect("community", SqliteCommunityRepository(settings.SQLITE_PATH))
        if _community_repo is None:
            # SQLite connect failed — fall back to the in-memory mock so the
            # router stays alive rather than 503'ing every community request.
            from app.db.mock.community import MockCommunityRepository

            _community_repo = MockCommunityRepository()
            _degraded.discard("community")
    else:
        # No real Dynamo impl yet — use the in-memory mock as a fallback.
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
    if _quest_repo and hasattr(_quest_repo, "close"):
        await _quest_repo.close()  # type: ignore[union-attr]
    if _platform_settings_repo and hasattr(_platform_settings_repo, "close"):
        await _platform_settings_repo.close()  # type: ignore[union-attr]
    if _tag_repo and hasattr(_tag_repo, "close"):
        await _tag_repo.close()  # type: ignore[union-attr]
    if _audit_repo and hasattr(_audit_repo, "close"):
        await _audit_repo.close()  # type: ignore[union-attr]
    if _community_repo and hasattr(_community_repo, "close"):
        await _community_repo.close()

    # Tear down the shared aioboto3 DynamoDB resource — only matters when the
    # backend is dynamodb (sqlite repos don't touch it). Safe to call either
    # way: close_shared_resource() is a no-op if nothing was opened.
    if settings.DB_BACKEND == "dynamodb":
        from app.db.dynamo._session import close_shared_resource

        await close_shared_resource()


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


def get_community_repo() -> CommunityRepository:
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


def get_quest_repo() -> QuestRepository | None:
    return _quest_repo


def get_platform_settings_repo() -> PlatformSettingsRepository | None:
    return _platform_settings_repo


def get_tag_repo() -> TagRepository | None:
    return _tag_repo


def get_audit_repo() -> AuditRepository | None:
    return _audit_repo


def degraded_domains() -> set[str]:
    """Read-only view for /health and debugging."""
    return set(_degraded)
