"""Ban enforcement — standardized 403 responses for user/community bans."""

from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status


def _is_expired(expires_at: str | None) -> bool:
    """Return True if expires_at is set and in the past (ban has expired)."""
    if not expires_at:
        return False  # no expiration = permanent ban, still in effect
    try:
        exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        return exp <= datetime.now(UTC)
    except (ValueError, TypeError):
        return False


def _ban_detail(code: str, message: str, expires_at: str | None = None) -> dict[str, Any]:
    """Standard 403 detail structure for frontend detection."""
    d: dict[str, Any] = {"code": code, "message": message}
    if expires_at:
        d["expires_at"] = expires_at
    return d


def raise_if_user_banned(record: dict[str, Any]) -> None:
    """Raise HTTP 403 with USER_BANNED if the user is banned (account-wide)."""
    st = record.get("status")
    if st != "banned":
        return
    expires = record.get("status_expiration")
    if expires and _is_expired(expires):
        return  # ban expired
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=_ban_detail(
            "USER_BANNED",
            "Account suspended",
            record.get("status_expiration"),
        ),
    )


def raise_if_community_banned(record: dict[str, Any]) -> None:
    """Raise HTTP 403 with COMMUNITY_BANNED if the user is community-banned."""
    st = record.get("community_status")
    if st != "banned":
        return
    expires = record.get("community_status_expiration")
    if expires and _is_expired(expires):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=_ban_detail(
            "COMMUNITY_BANNED",
            "Community access suspended",
            record.get("community_status_expiration"),
        ),
    )
