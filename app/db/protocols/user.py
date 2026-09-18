from typing import Any, Protocol


class UserAlreadyExistsError(Exception):
    """Raised by ``create_user`` when a row with the given ``id`` (or a
    uniqueness constraint it owns, e.g. ``auth0_id``) already exists.

    Used by the auto-provisioning path (``app/auth/dependencies.py``,
    ``get_registered_user``) to detect a concurrent first-touch race — two
    authenticated requests for the same not-yet-registered identity landing
    close enough together that both attempt to provision. The caller
    re-fetches by id and treats the loser's create as a no-op rather than
    surfacing an error.
    """


class UserRepository(Protocol):
    # -- User record --

    async def create_user(self, user: dict[str, Any]) -> dict[str, Any]:
        """Insert a new user record.

        ``user`` may include an explicit ``id`` (the auto-provisioning path
        passes a deterministic one so concurrent callers converge on the
        same row); when absent, a fresh UUID is generated.

        Raises ``UserAlreadyExistsError`` if a row with this ``id`` already
        exists, or (SQLite backend only) if ``auth0_id``/``username``
        collide with an existing row.
        """
        ...

    async def get_user_by_auth0_id(self, auth0_id: str) -> dict[str, Any] | None:
        """Look up a user by their Auth0 sub claim."""
        ...

    async def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        """Look up a user by internal UUID."""
        ...

    async def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        """Look up a user by unique username (for public profiles, etc.)."""
        ...

    async def update_user(
        self, user_id: str, patch: dict[str, Any], *, current: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Merge *patch* into the user record and return the full result.

        When the caller already holds a freshly-read record (e.g. the lesson
        batch path reads the row once at the top), pass it as ``current`` to
        skip the implementation's own read-before-write — saves one GetItem on
        the hot path. The caller owns the freshness guarantee.
        """
        ...

    async def list_users(
        self,
        limit: int = 100,
        cursor: str | None = None,
        *,
        search: str | None = None,
        status: str | None = None,
        community_status: str | None = None,
        sort: str = "created_at",
        order: str = "desc",
    ) -> tuple[list[dict[str, Any]], str | None]:
        """List users for admin. Returns (items, next_cursor). next_cursor is None when no more.

        Optional filters:
          - ``search`` matches against username/display_name (case-insensitive substring)
          - ``status`` filters by ``status`` column
          - ``community_status`` filters by ``community_status`` column
          - ``sort`` one of ``created_at`` | ``last_active_date`` | ``xp``
          - ``order`` one of ``asc`` | ``desc``
        """
        ...

    async def user_stats(self, *, since_days: int = 7) -> dict[str, int]:
        """Return aggregate user counts: {"total", "new_since", "active_since"}.

        ``new_since`` counts users created within the last ``since_days`` days;
        ``active_since`` counts users whose ``last_active_date`` falls in the
        same window.
        """
        ...

    async def delete_user(self, user_id: str) -> None:
        """Delete a user and their settings. No-op if user does not exist."""
        ...

    # -- User settings --

    async def get_settings(self, user_id: str) -> dict[str, Any] | None:
        """Return the user's settings dict, or None if no record exists."""
        ...

    async def update_settings(self, user_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """Merge *patch* into the user's settings and return the full result."""
        ...
