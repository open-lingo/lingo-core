"""Role system for moderation and governance.

Roles define what a user can do. Status defines whether they can act.
See docs/MODERATION_DESIGN.md for the full model.

Future: roles may map to OAuth scopes. For now they are stored in our DB.
"""

from enum import StrEnum


class Role(StrEnum):
    """User roles. Order matters for comparison (higher = more privilege)."""

    USER = "user"
    TRUSTED_CREATOR = "trusted_creator"
    MODERATOR = "moderator"
    ADMIN = "admin"
    SUPER_ADMIN = "super_admin"


# Roles that can access admin/moderation endpoints
ADMIN_ROLES = frozenset({Role.ADMIN, Role.SUPER_ADMIN})

# Roles that can review content (future)
MODERATOR_ROLES = frozenset({Role.MODERATOR, Role.ADMIN, Role.SUPER_ADMIN})


def has_admin_access(role: str | None) -> bool:
    """True if role can access admin endpoints.

    Fix 4: env-driven allow-list owns the actual gate (see
    ``app.auth.dependencies.require_admin``). This role check is the legacy
    DB-role path; it returns True only for explicit admin roles so the
    require_admin dependency can OR the two together.
    """
    return role in ADMIN_ROLES if role else False


def user_id_is_admin(user_id: str | None, auth0_sub: str | None) -> bool:
    """True if the user appears in ``settings.ADMIN_USER_IDS`` either by
    internal UUID or by Auth0 sub. Imported lazily to avoid a settings
    import cycle at module load."""
    from app.config import settings

    allow = set(settings.ADMIN_USER_IDS or [])
    if not allow:
        return False
    if user_id and user_id in allow:
        return True
    if auth0_sub and auth0_sub in allow:
        return True
    return False


def has_moderator_access(role: str | None) -> bool:
    """True if role can moderate content (future)."""
    return role in MODERATOR_ROLES if role else False
