"""API v1 — aggregates all domain routers under a single versioned prefix.

Mounted in main.py at /api/core/v1, giving routes like:
  GET /api/core/v1/srs/state
  POST /api/core/v1/users/me
  GET /api/core/v1/decks
  ...

To introduce v2, create app/v2/router.py, import its router in main.py,
and mount it at /api/core/v2.  Domain routers can be shared or overridden
per version as needed.
"""

from fastapi import APIRouter

from app.admin.ban_router import router as admin_ban_router
from app.admin.router import router as admin_router
from app.admin.social_router import router as admin_social_router
from app.admin.xp_router import router as admin_xp_router
from app.community.router import router as community_router
from app.decks.router import router as decks_router
from app.finance.router import router as finance_router
from app.platform_settings.router import router as platform_settings_router
from app.progress.router import router as progress_router
from app.quests.router import router as quests_router
from app.social.router import router as social_router
from app.srs.router import router as srs_router
from app.stories.router import router as stories_router
from app.tags.router import admin_router as tags_admin_router
from app.tags.router import public_router as tags_public_router
from app.users.router import router as users_router

v1_router = APIRouter()

v1_router.include_router(srs_router, prefix="/srs")
v1_router.include_router(users_router, prefix="/users")
v1_router.include_router(decks_router, prefix="/decks")
v1_router.include_router(stories_router, prefix="/stories")
v1_router.include_router(community_router, prefix="/community")
v1_router.include_router(admin_router, prefix="/admin")
v1_router.include_router(admin_social_router, prefix="/admin/social")
v1_router.include_router(admin_xp_router, prefix="/admin")
v1_router.include_router(admin_ban_router, prefix="/admin")
v1_router.include_router(finance_router, prefix="/finance")
v1_router.include_router(progress_router, prefix="/progress")
v1_router.include_router(social_router, prefix="/social")
v1_router.include_router(quests_router, prefix="/quests")
v1_router.include_router(platform_settings_router, prefix="/admin/platform-settings")
v1_router.include_router(tags_public_router, prefix="/tags")
v1_router.include_router(tags_admin_router, prefix="/admin/tags")
