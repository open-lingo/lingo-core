"""Pydantic schemas for the rewarded-ad credit endpoint.

v1 shape is deliberately tiny: the FE sends a client-generated idempotency
key + the placement that triggered the watch, and we return the credited
amount + the new balance. Future versions will likely add watch duration,
ad-network attribution, and a server-side cap — none of that lives here yet.
"""

from pydantic import BaseModel, Field


class WatchedAdPayload(BaseModel):
    """Client-supplied payload for POST /ads/watched."""

    # UUIDv4 from the FE, scoped to (user_id, idempotency_key) on the server.
    # We don't validate the UUID format — any short stable string the FE
    # generates is fine; the cap is just to keep memory bounded if the FE
    # accidentally sends a giant blob.
    idempotency_key: str = Field(..., min_length=1, max_length=128)
    # Free-form placement slug so we can slice ad revenue per surface later.
    placement: str = Field(..., min_length=1, max_length=64)


class WatchedAdResponse(BaseModel):
    """Successful credit response — what the FE renders next to the lingot icon."""

    lingots_awarded: int
    new_balance: int
