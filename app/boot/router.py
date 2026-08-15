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

from fastapi import APIRouter, Depends, HTTPException

from app.auth.dependencies import get_acting_user
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
    user: CurrentUser,
    users: UserRepo,
    progress: ProgressRepo,
    srs: SRSRepo,
    quests: QuestRepo,
    subscriptions: SubscriptionRepo,
) -> Any:
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
        _best_effort(list_quests(user, quests)),
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
