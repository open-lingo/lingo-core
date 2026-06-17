"""Database provider — wires concrete repository implementations for FastAPI DI.

At startup the app calls ``init_repositories()`` which reads ``DB_BACKEND``
from config and instantiates the right concrete class.  FastAPI dependencies
(``get_user_repo``, etc.) return the singleton for the lifetime of the app.

Community uses 5 separate Dynamo tables (threads/posts/votes/addons/markdown)
that match the SQLite split. The ``DynamoCommunityRepository`` takes a dict
mapping each domain to its table name so callers can override per-domain in
tests without rebuilding the prefix logic.

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
    LeaderboardRepository,
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
_leaderboard_repo: LeaderboardRepository | None = None
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
    global _leaderboard_repo

    _degraded.clear()

    if settings.DB_BACKEND == "sqlite":
        from app.db.sqlite.deck import SqliteDeckRepository
        from app.db.sqlite.leaderboard import SqliteLeaderboardRepository
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
        _leaderboard_repo = await _safe_connect("leaderboard", SqliteLeaderboardRepository(settings.SQLITE_PATH))
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
        from app.db.dynamo.leaderboard import DynamoLeaderboardRepository
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
        _deck_repo = await _safe_connect(
            "deck",
            DynamoDeckRepository(
                f"{prefix}decks",
                region,
                votes_table_name=f"{prefix}deck_votes",
            ),
        )
        _subscription_repo = await _safe_connect("subscription", DynamoSubscriptionRepository(f"{prefix}subscriptions", region))

        # Stories — real Dynamo impl backed by lingo_stories.
        from app.db.dynamo.story import DynamoStoryRepository

        _story_repo = await _safe_connect("story", DynamoStoryRepository(f"{prefix}stories", region))

        _progress_repo = await _safe_connect("progress", DynamoProgressRepository(f"{prefix}progress", region))

        # Real Dynamo impl backed by lingo_social (friends/requests/blocks/
        # activity/reactions/invites/DM threads). Cut over from SQLite-first.
        _social_repo = await _safe_connect("social", DynamoSocialRepository(f"{prefix}social", region))

        # Leaderboard read repo — backed by lingo_social_leaderboard (written by
        # lingo-async). lingo-core only reads it; the recompute-from-rollups
        # Scan path is gone (cost item 5).
        _leaderboard_repo = await _safe_connect(
            "leaderboard", DynamoLeaderboardRepository(f"{prefix}social_leaderboard", region)
        )

        _quest_repo = await _safe_connect("quest", DynamoQuestRepository(f"{prefix}quests", region))

        # Platform settings — real Dynamo impl backed by lingo_platform_settings.
        from app.db.dynamo.platform_settings import DynamoPlatformSettingsRepository

        _platform_settings_repo = await _safe_connect(
            "platform_settings",
            DynamoPlatformSettingsRepository(f"{prefix}platform_settings", region),
        )

        # Tags — real Dynamo impl backed by lingo_tags (tag CRUD + deck<->tag
        # association GSI). Cut over from SQLite-first.
        from app.db.dynamo.tag import DynamoTagRepository

        _tag_repo = await _safe_connect("tag", DynamoTagRepository(f"{prefix}tags", region))

        # Audit — real Dynamo impl backed by lingo_admin_audit (append +
        # cursor-paginated list). Cut over from SQLite-first.
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
        # Real Dynamo impl backed by the 5 community tables provisioned in
        # ``lingo-infra/main.tf``. No mock fallback — degraded community
        # surfaces 503 via the get_community_repo accessor.
        from app.db.dynamo.community import DynamoCommunityRepository

        _community_repo = await _safe_connect(
            "community",
            DynamoCommunityRepository(
                {
                    "threads": f"{prefix}community_threads",
                    "posts": f"{prefix}community_posts",
                    "votes": f"{prefix}community_votes",
                    "addons": f"{prefix}community_addons",
                    "markdown": f"{prefix}community_markdown",
                },
                region,
            ),
        )

    _assert_backend_wiring()

    if _degraded:
        logger.warning("Provider booted in degraded mode: %s", sorted(_degraded))


# Every domain the app serves. A repo singleton that is None outside
# ``_degraded`` (i.e. not a runtime connect failure) means a domain was left
# unwired — fail loudly rather than silently 503/return-[] in prod.
_REQUIRED_DOMAINS: dict[str, str] = {
    "user": "_user_repo",
    "srs": "_srs_repo",
    "deck": "_deck_repo",
    "subscription": "_subscription_repo",
    "story": "_story_repo",
    "progress": "_progress_repo",
    "social": "_social_repo",
    "leaderboard": "_leaderboard_repo",
    "quest": "_quest_repo",
    "platform_settings": "_platform_settings_repo",
    "tag": "_tag_repo",
    "audit": "_audit_repo",
    "community": "_community_repo",
}


def _assert_backend_wiring() -> None:
    """Fail loudly if any domain is mis-wired for the selected backend.

    Distinguishes a *runtime* connect failure (acceptable degraded mode,
    recorded in ``_degraded``) from a *wiring* bug — a domain repo that is
    None for no reason, or that resolved to the wrong concrete class
    (e.g. a Mock or a SQLite repo leaking into the dynamodb path). The
    latter would silently mask missing data behind 503/empty responses in
    prod; we want startup to crash instead.
    """
    g = globals()
    expected_prefix = "Dynamo" if settings.DB_BACKEND == "dynamodb" else "Sqlite"

    for domain, attr in _REQUIRED_DOMAINS.items():
        repo = g.get(attr)
        if repo is None:
            # None is only acceptable when connect() failed at runtime.
            if domain not in _degraded:
                raise RuntimeError(
                    f"Domain {domain!r} is unwired under DB_BACKEND="
                    f"{settings.DB_BACKEND!r}: repo is None but the domain "
                    f"is not in the degraded set. This is a wiring bug."
                )
            continue

        cls_name = type(repo).__name__
        # The community SQLite-connect-failure fallback to MockCommunityRepository
        # is the one sanctioned non-backend class; it self-records by clearing
        # itself from _degraded, so it only appears here for sqlite.
        if domain == "community" and cls_name == "MockCommunityRepository":
            if settings.DB_BACKEND == "dynamodb":
                raise RuntimeError(
                    "Community resolved to MockCommunityRepository under the "
                    "dynamodb backend — the mock is a sqlite-only fallback."
                )
            continue

        if not cls_name.startswith(expected_prefix):
            raise RuntimeError(
                f"Domain {domain!r} resolved to {cls_name!r} under "
                f"DB_BACKEND={settings.DB_BACKEND!r}; expected a "
                f"{expected_prefix}*Repository. Mis-wired backend."
            )


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
    if _leaderboard_repo and hasattr(_leaderboard_repo, "close"):
        await _leaderboard_repo.close()  # type: ignore[union-attr]
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
    if _community_repo is None:
        _raise_degraded("community")
    return _community_repo  # type: ignore[return-value]


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


def get_leaderboard_repo() -> LeaderboardRepository | None:
    return _leaderboard_repo


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
