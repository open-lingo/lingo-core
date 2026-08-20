"""API v1 — aggregates all domain routers under a single versioned prefix.

Mounted in main.py at /api/core/v1, giving routes like:
  GET /api/core/v1/srs/state
  POST /api/core/v1/users/me
  GET /api/core/v1/decks
  ...

To introduce v2, create app/v2/router.py, import its router in main.py,
and mount it at /api/core/v2.  Domain routers can be shared or overridden
per version as needed.

Surface mode (``SURFACE_MODE`` setting) selects WHICH routers get HTTP-mounted:
- ``full`` (default): every router — unchanged behavior.
- ``beta``: only the landing/sign-in/learn/practice core loop
  (boot, users, srs, progress). Un-mounting shrinks both the unauthenticated
  attack surface (the scan-backed public /community + /tags reads) AND the
  authenticated one (admin routes, which have no role enforcement yet — see
  CLAUDE.md), and it trims cold-start import cost rather than adding any. The
  handler FUNCTIONS stay importable, so /boot's internal calls into
  quests/progress/users/srs keep working even when those routers are unmounted.

``build_v1_router`` reads the mode at CALL time (main.py calls it), so the
conftest reload-the-app pattern picks up a test-set mode without also having
to reload this module.
"""

from fastapi import APIRouter

from app.admin.audit_router import router as admin_audit_router
from app.admin.ban_router import router as admin_ban_router
from app.admin.impersonate_router import router as admin_impersonate_router
from app.admin.lms_router import router as admin_lms_router
from app.admin.router import router as admin_router
from app.admin.social_router import router as admin_social_router
from app.admin.xp_router import router as admin_xp_router
from app.ads.router import router as ads_router
from app.boot.router import router as boot_router
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

# (router, prefix, group). ``group`` is the surface-mode key; a router is
# mounted when the active mode enables its group. Order preserved from the
# original mount sequence so route resolution is byte-identical in full mode.
_MOUNTS: list[tuple[APIRouter, str, str]] = [
    (boot_router, "/boot", "boot"),
    (srs_router, "/srs", "srs"),
    (users_router, "/users", "users"),
    (decks_router, "/decks", "decks"),
    (stories_router, "/stories", "stories"),
    (community_router, "/community", "community"),
    (admin_router, "/admin", "admin"),
    (admin_social_router, "/admin/social", "admin"),
    (admin_xp_router, "/admin", "admin"),
    (admin_lms_router, "/admin/lms", "admin"),
    (admin_ban_router, "/admin", "admin"),
    (admin_audit_router, "/admin", "admin"),
    (admin_impersonate_router, "/admin", "admin"),
    (finance_router, "/finance", "finance"),
    (progress_router, "/progress", "progress"),
    (social_router, "/social", "social"),
    (quests_router, "/quests", "quests"),
    (platform_settings_router, "/admin/platform-settings", "admin"),
    (tags_public_router, "/tags", "tags"),
    (tags_admin_router, "/admin/tags", "admin"),
    (ads_router, "/ads", "ads"),
]

# The landing/sign-in/learn/practice core loop. Landing + sign-in hit no v1
# router (Auth0 is external); learn is bundled content; practice = srs +
# progress; boot + users are the always-needed spine.
_BETA_GROUPS = frozenset({"boot", "users", "srs", "progress"})


def _enabled_groups(mode: str | None) -> frozenset[str] | None:
    """Return the set of enabled groups, or None meaning 'mount everything'.

    Unknown modes fall back to full (None) so a typo in the env var can never
    silently serve an empty API — surface reduction is explicit opt-in.
    """
    if (mode or "full").lower() == "beta":
        return _BETA_GROUPS
    return None


def build_v1_router(mode: str | None = None) -> APIRouter:
    """Construct the v1 aggregate router for the given surface mode.

    ``mode=None`` reads ``settings.SURFACE_MODE`` at call time.
    """
    if mode is None:
        from app.config import settings

        mode = settings.SURFACE_MODE
    enabled = _enabled_groups(mode)
    router = APIRouter()
    for sub_router, prefix, group in _MOUNTS:
        if enabled is None or group in enabled:
            router.include_router(sub_router, prefix=prefix)
    return router


# Module-level default (full unless SURFACE_MODE is set) kept for any importer
# of ``v1_router``; main.py calls ``build_v1_router()`` fresh so a reloaded app
# reflects the current mode.
v1_router = build_v1_router()
