"""Rewarded-ad credit endpoint.

POST /ads/watched — the FE calls this after a rewarded ad finishes playing.
We credit the user's lingot balance and fire an ``ad_watched`` event for the
inspector / future analytics.

v1 scope (locked by Spencer): no daily cap, no cooldown, no fraud-prevention.
Dedup is a per-process in-memory LRU keyed on ``(user_id, idempotency_key)``
purely to absorb FE double-clicks; it intentionally resets on server
restart. Real fraud + ad-time tracking lands when we wire to the ad SDK's
server-side reward callback.
"""

import logging
from collections import OrderedDict
from datetime import UTC, datetime
from threading import Lock
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.ads.schemas import WatchedAdPayload, WatchedAdResponse
from app.auth.dependencies import get_registered_user
from app.auth.schemas import TokenPayload
from app.db.protocols import UserRepository
from app.db.provider import get_user_repo
from app.events.publisher import publish as publish_event
from app.shared.errors import api_error
from app.shared.repos import require_repo

logger = logging.getLogger("lingo.ads")

router = APIRouter(tags=["ads"])

CurrentUser = Annotated[TokenPayload, Depends(get_registered_user)]
UserRepo = Annotated[UserRepository, Depends(get_user_repo)]

# Tweakable: flat credit per rewarded-ad watch. Lives here as a module
# constant so a future config-driven knob (per placement, per region) can
# swap the lookup without touching the route.
LINGOTS_PER_AD = 5

# ── Dedup ────────────────────────────────────────────────────────────────────
# v1 dedup: in-memory LRU. Goal is to absorb a double-click between the FE
# fire-and-forget POST and a retry — NOT to prevent a determined replayer.
# Resets on server restart (acceptable: worst case is one extra credit per
# deploy per simultaneous double-click). When we move to real fraud
# prevention, this gets replaced by a server-side reward-callback ledger.

_DEDUP_CAP = 1000
_dedup_seen: "OrderedDict[tuple[str, str], None]" = OrderedDict()
_dedup_lock = Lock()


def _dedup_check_and_record(user_id: str, idempotency_key: str) -> bool:
    """Return True if this (user, key) pair was already credited; False otherwise.

    Recording is atomic with the check under a single lock so two concurrent
    requests can't both win. LRU eviction keeps the set bounded.
    """
    key = (user_id, idempotency_key)
    with _dedup_lock:
        if key in _dedup_seen:
            # Move-to-end keeps recently-seen keys live so the FE's
            # immediate retry path still hits dedup.
            _dedup_seen.move_to_end(key)
            return True
        _dedup_seen[key] = None
        if len(_dedup_seen) > _DEDUP_CAP:
            _dedup_seen.popitem(last=False)
        return False


def _reset_dedup_for_tests() -> None:
    """Test-only helper — clears the in-process dedup set."""
    with _dedup_lock:
        _dedup_seen.clear()


# ── Endpoint ─────────────────────────────────────────────────────────────────


@router.post("/watched", response_model=WatchedAdResponse)
async def watched_ad(
    body: WatchedAdPayload,
    user: CurrentUser,
    users: UserRepo,
) -> Any:
    """Credit ``LINGOTS_PER_AD`` lingots for a finished rewarded-ad watch."""
    repo = require_repo(users, "user")

    if _dedup_check_and_record(user.id, body.idempotency_key):
        # v1 dedup hit — FE double-click or retry. Return 429 with a stable
        # detail so the FE can show a soft "already credited" toast.
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="already_credited",
        )

    with api_error("crediting watched ad"):
        record = await repo.get_user_by_id(user.id)
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

        base = int(record.get("lingots") or 0)
        new_balance = base + LINGOTS_PER_AD
        await repo.update_user(user.id, {"lingots": new_balance})

    # Best-effort event publish — a broker hiccup must not block the credit.
    # The async worker doesn't have a handler for ``ad_watched`` yet; the
    # event-store inspector picks it up by virtue of being captured.
    try:
        publish_event(
            {
                "type": "ad_watched",
                "version": 1,
                "user_id": user.id,
                "placement": body.placement,
                "idempotency_key": body.idempotency_key,
                "lingots_awarded": LINGOTS_PER_AD,
                "occurred_at": datetime.now(UTC).isoformat(),
            }
        )
    except Exception:  # noqa: BLE001 — publish failures never break the credit
        logger.exception("ad_watched publish failed user_id=%s", user.id)

    return WatchedAdResponse(
        lingots_awarded=LINGOTS_PER_AD,
        new_balance=new_balance,
    )
