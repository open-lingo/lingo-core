from pydantic import BaseModel


class TokenPayload(BaseModel):
    """Relevant claims extracted from a validated Auth0 JWT."""

    sub: str
    permissions: list[str] = []
