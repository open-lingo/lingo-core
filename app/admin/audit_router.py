"""Admin audit log — read + helper for write callsites.

Append-only log of admin actions. Write side is best-effort (failures
never poison the underlying admin action); read side serves both the
home-page tail and a dedicated /admin/ops/audit history view.

Wire the helper ``record_admin_action`` from every endpoint that mutates
user state (ban/unban/award-xp/deck-approve/etc.) so the trail is
populated automatically.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.dependencies import require_admin
from app.auth.schemas import TokenPayload
from app.db.protocols import AuditRepository
from app.db.provider import get_audit_repo
from app.shared.errors import api_error

logger = logging.getLogger("lingo.admin.audit")

router = APIRouter(tags=["admin", "audit"])

AdminUser = Annotated[TokenPayload, Depends(require_admin)]
AuditRepo = Annotated[AuditRepository | None, Depends(get_audit_repo)]


@router.get("/audit")
async def list_audit(
    _admin: AdminUser,
    repo: AuditRepo,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None),
    actor_id: str | None = Query(None),
    target_kind: str | None = Query(None),
) -> dict[str, Any]:
    """List audit entries newest-first. Returns {items, nextCursor}."""
    if repo is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "audit repo unavailable")
    with api_error("listing audit log"):
        items, next_cursor = await repo.list(
            limit=limit,
            cursor=cursor,
            actor_id=actor_id,
            target_kind=target_kind,
        )
    return {"items": items, "nextCursor": next_cursor}


async def record_admin_action(
    *,
    actor_id: str | None,
    action: str,
    target_id: str | None,
    target_kind: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Append an audit entry — best-effort, never raises.

    Callsites pass ``admin.id`` (the moderator's UUID) as ``actor_id``,
    the verb (``"ban"``/``"unban"``/``"deck_approve"``…) as ``action``,
    and the relevant scrubbed payload as ``payload`` (reasons,
    durations, etc.). Failures get logged and swallowed so a degraded
    audit log doesn't break the admin write path.
    """
    repo = get_audit_repo()
    if repo is None:
        return
    try:
        await repo.append(
            actor_id=actor_id or "",
            action=action,
            target_id=target_id,
            target_kind=target_kind,
            payload=payload,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort write
        logger.warning(
            "Audit append failed for action=%s target=%s/%s: %s",
            action,
            target_kind,
            target_id,
            exc,
        )
