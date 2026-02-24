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
    """True if role can access admin endpoints."""
    # TODO: Enable once OAuth scopes are set up. For now everyone is admin.
    return True  # role in ADMIN_ROLES if role else False


def has_moderator_access(role: str | None) -> bool:
    """True if role can moderate content (future)."""
    return role in MODERATOR_ROLES if role else False
