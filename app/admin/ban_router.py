"""Admin ban / unban — moderation-history-aware writes.

Lives in its own router so ``app/admin/router.py`` stays scannable. The
ban history shape matches the ``project-moderation-history`` memory:

- ``account_ban_history`` and ``community_ban_history`` are lists capped
  at 2 entries each. When a third ban happens, the oldest is dropped.
- Duration is one of 24h / 7d / 30d / permanent. Permanent ⇒ no expiry.
- Unban closes the most-recent open record (``ended_at = now()``) and
  flips the corresponding ``status``/``community_status`` field back to
  ``"active"``.

All routes gate on ``require_admin`` and the moderator's user_id is
stamped onto the ban record for the audit trail.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.admin.audit_router import record_admin_action
from app.auth.dependencies import require_admin
from app.auth.schemas import TokenPayload
from app.db.protocols import UserRepository
from app.db.provider import get_user_repo
from app.shared.errors import api_error
from app.shared.repos import require_repo
from app.users.schemas import BanRecord, UserResponse

router = APIRouter(tags=["admin", "moderation"])

AdminUser = Annotated[TokenPayload, Depends(require_admin)]
UserRepo = Annotated[UserRepository | None, Depends(get_user_repo)]

BanKind = Literal["account", "community"]
BanDuration = Literal["24h", "7d", "30d", "permanent"]

_DURATION_DELTAS: dict[BanDuration, timedelta | None] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "permanent": None,
}

# Max history entries per type, per memory. When the third ban is
# written, the oldest entry is dropped so the row stays bounded.
_MAX_HISTORY = 2


class BanRequest(BaseModel):
    type: BanKind
    reason: str = Field(min_length=1, max_length=500)
    duration: BanDuration
    notes: str | None = Field(default=None, max_length=2000)


class UnbanRequest(BaseModel):
    type: BanKind
    notes: str | None = Field(default=None, max_length=2000)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _history_field(kind: BanKind) -> str:
    return "account_ban_history" if kind == "account" else "community_ban_history"


def _status_field(kind: BanKind) -> tuple[str, str]:
    """Return (status_col, expiration_col) for the given kind."""
    if kind == "account":
        return "status", "status_expiration"
    return "community_status", "community_status_expiration"


def _expiry_for(duration: BanDuration) -> str | None:
    delta = _DURATION_DELTAS[duration]
    if delta is None:
        return None
    return (datetime.now(UTC) + delta).isoformat()


def _append_capped(history: list[dict[str, Any]] | None, record: dict[str, Any]) -> list[dict[str, Any]]:
    """Append a record and keep only the most recent ``_MAX_HISTORY`` entries."""
    rows = list(history or [])
    rows.append(record)
    if len(rows) > _MAX_HISTORY:
        rows = rows[-_MAX_HISTORY:]
    return rows


@router.post("/users/{user_id}/ban", response_model=UserResponse)
async def admin_ban_user(
    user_id: str,
    body: BanRequest,
    admin_user: AdminUser,
    repo: UserRepo,
) -> Any:
    """Ban ``user_id`` (account-wide or community-only).

    Appends a ``BanRecord`` to the relevant history list and flips the
    matching ``status`` field. The two-entry cap is enforced server-side
    so older entries don't survive a third ban.
    """
    r = require_repo(repo, "user")
    with api_error("banning user"):
        existing = await r.get_user_by_id(user_id)
        if existing is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

        if admin_user.id == user_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Cannot ban yourself")

        now = _now_iso()
        expires_at = _expiry_for(body.duration)
        record = BanRecord(
            reason=body.reason,
            started_at=now,
            expires_at=expires_at,
            ended_at=None,
            moderator_id=admin_user.id or "",
            notes=body.notes,
        ).model_dump(mode="json")

        history_field = _history_field(body.type)
        status_col, expiration_col = _status_field(body.type)
        new_history = _append_capped(existing.get(history_field), record)

        patch: dict[str, Any] = {
            history_field: new_history,
            status_col: "banned",
            expiration_col: expires_at,
        }
        updated = await r.update_user(user_id, patch)

    await record_admin_action(
        actor_id=admin_user.id,
        action=f"ban_{body.type}",
        target_id=user_id,
        target_kind="user",
        payload={
            "reason": body.reason,
            "duration": body.duration,
            "expires_at": expires_at,
        },
    )
    return UserResponse(**updated)


@router.post("/users/{user_id}/unban", response_model=UserResponse)
async def admin_unban_user(
    user_id: str,
    body: UnbanRequest,
    admin_user: AdminUser,
    repo: UserRepo,
) -> Any:
    """Unban ``user_id``.

    Closes the most recent open ban record (``ended_at = now``) and
    flips the corresponding status field back to ``active`` (account)
    or ``None`` (community). No-op if no active ban is present, but the
    status flip still applies.
    """
    r = require_repo(repo, "user")
    with api_error("unbanning user"):
        existing = await r.get_user_by_id(user_id)
        if existing is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

        now = _now_iso()
        history_field = _history_field(body.type)
        status_col, expiration_col = _status_field(body.type)

        history = list(existing.get(history_field) or [])
        for record in reversed(history):
            if not record.get("ended_at"):
                record["ended_at"] = now
                if body.notes and not record.get("notes"):
                    record["notes"] = body.notes
                break

        patch: dict[str, Any] = {
            history_field: history,
            status_col: "active" if body.type == "account" else None,
            expiration_col: None,
        }
        updated = await r.update_user(user_id, patch)

    await record_admin_action(
        actor_id=admin_user.id,
        action=f"unban_{body.type}",
        target_id=user_id,
        target_kind="user",
        payload={"notes": body.notes},
    )
    return UserResponse(**updated)
