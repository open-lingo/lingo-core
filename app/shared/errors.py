"""Centralized error handling for FastAPI routers.

Wrap blocks that talk to repos / external services with `api_error("doing X")`.
- HTTPException passes through unchanged (don't double-wrap deliberate raises).
- Anything else is logged with full traceback and surfaced as 500 with a
  generic detail string. No exception internals leak to the client.
"""

from collections.abc import Iterator
from contextlib import contextmanager
import logging

from fastapi import HTTPException

log = logging.getLogger("lingo.errors")


@contextmanager
def api_error(context: str) -> Iterator[None]:
    """Convert non-HTTP exceptions to 500 with a context label.

    Usage:
        with api_error("creating deck"):
            result = await repo.create_deck(...)
    """
    try:
        yield
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("api_error: %s", context)
        raise HTTPException(status_code=500, detail=f"Error {context}") from exc
