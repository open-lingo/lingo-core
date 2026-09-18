from pydantic import BaseModel


class TokenPayload(BaseModel):
    """Relevant claims extracted from a validated Auth0 JWT.

    ``sub`` is the raw Auth0 subject — only used for auth operations.
    ``id`` is our internal user UUID resolved from the DB after validation;
    all domain logic should use ``id``, never ``sub``.
    ``id`` is None until a DB row exists for this identity — either full
    registration (``POST /users/me``) or the auto-provisioned placeholder
    row a not-yet-registered caller gets on first touch of any route behind
    ``get_registered_user`` (``display_name == ""``; see
    ``app/auth/dependencies.py::_provision_user``).
    """

    sub: str
    id: str | None = None
    permissions: list[str] = []
    # When an admin impersonates a target user via X-Impersonate-User-Id,
    # ``get_acting_user`` swaps ``sub`` / ``id`` to the target's and
    # records the admin's originals here so audit logging + any
    # "who really did this" checks downstream can still see them. Both
    # are None on non-impersonated requests.
    actor_id: str | None = None
    actor_sub: str | None = None
