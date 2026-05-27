"""Tag API.

Three surfaces:

  GET    /api/core/v1/tags             — public, list canonical tags
  POST   /api/core/v1/admin/tags       — admin, create a tag
  PATCH  /api/core/v1/admin/tags/{slug}— admin, update a tag
  DELETE /api/core/v1/admin/tags/{slug}— admin, delete a tag (cascades on
                                          deck_tags via the SQLite impl)

All admin mutations require the same ``require_admin`` dep as the rest of
the admin surface. Slugs are validated by the Pydantic ``SLUG_PATTERN``
regex; duplicate creates return 409.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.dependencies import require_admin
from app.auth.schemas import TokenPayload
from app.db.protocols import TagRepository
from app.db.provider import get_tag_repo
from app.shared.errors import api_error
from app.shared.repos import require_repo
from app.tags.schemas import TagCreate, TagResponse, TagUpdate

# Two routers — one public read, one admin mutation. Mounted under
# different prefixes in app/v1/router.py.
public_router = APIRouter(tags=["tags"])
admin_router = APIRouter(tags=["tags-admin"])

TagRepo = Annotated[TagRepository | None, Depends(get_tag_repo)]
AdminUser = Annotated[TokenPayload, Depends(require_admin)]


def _to_response(row: dict) -> TagResponse:
    return TagResponse(
        slug=row["slug"],
        display_name=row["display_name"],
        description=row.get("description"),
        color=row.get("color"),
        created_at=row.get("created_at"),
    )


# ── Public ──────────────────────────────────────────────────────────────────


@public_router.get("", response_model=list[TagResponse])
async def list_tags(repo: TagRepo) -> list[TagResponse]:
    """List all canonical tags. Public — no auth required."""
    r = require_repo(repo, "tags")
    with api_error("listing tags"):
        rows = await r.list_tags()
    return [_to_response(row) for row in rows]


# ── Admin ───────────────────────────────────────────────────────────────────


@admin_router.post("", response_model=TagResponse, status_code=status.HTTP_201_CREATED)
async def create_tag(
    body: TagCreate,
    repo: TagRepo,
    _admin: AdminUser,
) -> TagResponse:
    """Create a new canonical tag. Admin only."""
    r = require_repo(repo, "tags")
    with api_error("creating tag"):
        try:
            row = await r.create_tag(
                slug=body.slug,
                display_name=body.display_name,
                description=body.description,
                color=body.color,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
    return _to_response(row)


@admin_router.patch("/{slug}", response_model=TagResponse)
async def update_tag(
    slug: str,
    body: TagUpdate,
    repo: TagRepo,
    _admin: AdminUser,
) -> TagResponse:
    """Patch a canonical tag. Admin only. 404 on missing slug."""
    r = require_repo(repo, "tags")
    with api_error("updating tag"):
        updated = await r.update_tag(
            slug,
            display_name=body.display_name,
            description=body.description,
            color=body.color,
        )
    if not updated:
        raise HTTPException(status_code=404, detail="tag not found")
    return _to_response(updated)


@admin_router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(
    slug: str,
    repo: TagRepo,
    _admin: AdminUser,
) -> None:
    """Delete a canonical tag and cascade-remove from deck_tags. Admin only."""
    r = require_repo(repo, "tags")
    with api_error("deleting tag"):
        deleted = await r.delete_tag(slug)
    if not deleted:
        raise HTTPException(status_code=404, detail="tag not found")
    return None
