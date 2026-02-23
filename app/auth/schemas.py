from pydantic import BaseModel


class TokenPayload(BaseModel):
    """Relevant claims extracted from a validated Auth0 JWT.

    ``sub`` is the raw Auth0 subject — only used for auth operations.
    ``id`` is our internal user UUID resolved from the DB after validation;
    all domain logic should use ``id``, never ``sub``.
    ``id`` is None until the user completes first-time registration.
    """

    sub: str
    id: str | None = None
    permissions: list[str] = []
