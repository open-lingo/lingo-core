"""Shared helper for asserting a repository is initialized."""

from typing import TypeVar

from fastapi import HTTPException, status

T = TypeVar("T")


def require_repo(repo: T | None, name: str) -> T:
    """Return the repo or raise 503 with a clear message if not initialized.

    Usage:
        repo = require_repo(provider.get_*_repo(), "decks")
    """
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{name} repository not available",
        )
    return repo
