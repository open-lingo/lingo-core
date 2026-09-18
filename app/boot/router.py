"""Batched boot read — one request for the client's whole boot sequence.

Why this exists (2026-08-15, measured in prod): the frontend's boot fired
six parallel authed GETs (users/me, settings, progress/me, unlocks, touch,
srs/state) the moment the token arrived. On Lambda, six *concurrent*
requests fan out to six instances, so a cold morning paid six full cold
starts (~2.4–2.9 s EACH, measured); even warm, each request pays the
per-invoke overhead (~0.6 s). Batching collapses that to ONE invoke: one
cold start worst-case, one invoke overhead always — the DynamoDB reads
inside were already cheap and now run under a single ``asyncio.gather``.

This router deliberately CALLS THE EXISTING ROUTE HANDLERS rather than
re-implementing their reads: /boot can never drift from what the
individual endpoints return, and any fix to them is a fix here. The
handlers are plain async functions (FastAPI's decorator registers and
returns them unchanged), so passing the resolved deps through works.

Contract notes:
- 404 when the user record doesn't exist (same as GET /users/me): a
  brand-new signup must keep the client's create-user flow; the client
  treats a /boot failure as "fall back to individual calls".
- ``quests``/``subscriptions`` are best-effort (see BootResponse).
"""

import asyncio
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth.dependencies import get_acting_user, sync_request_timezone
from app.auth.schemas import TokenPayload
from app.boot.schemas import BootResponse
from app.db.protocols import (
    ProgressRepository,
    QuestRepository,
    SRSRepository,
    SubscriptionRepository,
    UserRepository,
)
from app.db.provider import (
    get_progress_repo,
    get_quest_repo,
    get_srs_repo,
    get_subscription_repo,
    get_user_repo,
)
from app.progress.router import get_my_progress, get_unlock_map, touch_session
from app.quests.router import list_quests
from app.srs.router import get_state
from app.users.router import get_me, get_settings, list_subscriptions

router = APIRouter()

CurrentUser = Annotated[TokenPayload, Depends(get_acting_user)]
UserRepo = Annotated[UserRepository, Depends(get_user_repo)]
ProgressRepo = Annotated[ProgressRepository, Depends(get_progress_repo)]
SRSRepo = Annotated[SRSRepository, Depends(get_srs_repo)]
QuestRepo = Annotated[QuestRepository | None, Depends(get_quest_repo)]
SubscriptionRepo = Annotated[SubscriptionRepository | None, Depends(get_subscription_repo)]

T = TypeVar("T")


async def _best_effort(coro: Any) -> Any:
    """None instead of an HTTP error for the optional sections."""
    try:
        return await coro
    except HTTPException:
        return None


@router.get("", response_model=BootResponse)
async def get_boot(
    request: Request,
    user: CurrentUser,
    users: UserRepo,
    progress: ProgressRepo,
    srs: SRSRepo,
    quests: QuestRepo,
    subscriptions: SubscriptionRepo,
) -> Any:
    # Keep the caller's device timezone in sync (best-effort, LWW) before
    # the reads below — `list_quests` (in the gather) reads this same
    # user's STORED timezone to bucket daily/weekly resets by local
    # calendar day, so this write must land first, not race it. One
    # authenticated GET here per session/app-open is the sync point (see
    # `sync_request_timezone`'s docstring for why this isn't hooked into
    # every authenticated route instead).
    #
    # Syncs to the REAL caller's own row (`actor_id` when an admin is
    # impersonating via X-Impersonate-User-Id) — the header describes the
    # physical device making THIS request, which is the admin's, not the
    # impersonation target's.
    sync_user_id = user.actor_id or user.id
    if sync_user_id:
        sync_record = await users.get_user_by_id(sync_user_id)
        await sync_request_timezone(request, users, sync_user_id, sync_record)

    (
        me,
        settings,
        progress_summary,
        unlocks,
        touch,
        srs_state,
        quest_list,
        subscription_list,
    ) = await asyncio.gather(
        get_me(user, users),
        get_settings(user, users),
        get_my_progress(user, progress, users),
        get_unlock_map(user, users),
        touch_session(user, progress, users),
        get_state(user, srs),
        _best_effort(list_quests(user, quests, users)),
        _best_effort(list_subscriptions(user, subscriptions, None)),
    )
    return {
        "user": me,
        "settings": settings,
        "progress": progress_summary,
        "unlocks": unlocks,
        "touch": touch,
        "srs": srs_state,
        "quests": quest_list,
        "subscriptions": subscription_list,
    }
