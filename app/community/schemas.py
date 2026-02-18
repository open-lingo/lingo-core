"""Pydantic schemas for community/forum API.

Compatible with React markdown editor: body_markdown stores raw markdown.
"""

from pydantic import BaseModel, Field

# ── Categories & Tags ──


class CategoryResponse(BaseModel):
    id: str
    slug: str
    name_key: str
    description_key: str
    sort_order: int = 0
    created_at: str
    updated_at: str


class TagResponse(BaseModel):
    id: str
    slug: str
    name: str
    color: str | None = None
    created_at: str | None = None


class TagCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=50)
    color: str | None = None


# ── Threads ──

CONTENT_TYPES = (
    "official_course",
    "official_lesson",
    "official_module",
    "addon",
    "flashcard_pack",
    "story",
    "grammar",
)


class ThreadCreate(BaseModel):
    """Create a new forum thread."""

    category_id: str
    title: str = Field(min_length=1, max_length=300)
    excerpt: str | None = Field(default=None, max_length=500)
    body_markdown: str = Field(default="")
    tag_ids: list[str] = Field(default_factory=list, max_length=10)
    content_links: list["ContentLinkCreate"] = Field(default_factory=list, max_length=5)


class ContentLinkCreate(BaseModel):
    content_type: str = Field(
        description="official_course, official_lesson, addon, flashcard_pack, etc."
    )
    content_id: str
    language_id: str | None = None


ThreadCreate.model_rebuild()


class ThreadResponse(BaseModel):
    id: str
    category_id: str
    author_id: str
    author_name: str
    title: str
    excerpt: str
    body_markdown: str
    reply_count: int = 0
    upvote_count: int = 0
    downvote_count: int = 0
    view_count: int = 0
    is_pinned: bool = False
    status: str = "open"
    tag_ids: list[str] = Field(default_factory=list)
    content_links: list["ContentLinkResponse"] = Field(default_factory=list)
    created_at: str
    updated_at: str


class ContentLinkResponse(BaseModel):
    id: str
    thread_id: str
    content_type: str
    content_id: str
    language_id: str | None = None
    created_at: str


class ThreadPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    excerpt: str | None = Field(default=None, max_length=500)
    body_markdown: str | None = None
    status: str | None = None


# ── Posts ──


class PostCreate(BaseModel):
    """Create a reply (post) in a thread. thread_id comes from path."""

    parent_id: str | None = None
    body_markdown: str = Field(default="")


class PostResponse(BaseModel):
    id: str
    thread_id: str
    parent_id: str | None = None
    author_id: str
    author_name: str
    body_markdown: str
    upvote_count: int = 0
    downvote_count: int = 0
    created_at: str
    updated_at: str


class PostPatch(BaseModel):
    body_markdown: str | None = None


# ── Votes ──


class VoteRequest(BaseModel):
    value: int = Field(description="1 for upvote, -1 for downvote")


# ── Addons ──

ADDON_KINDS = ("course", "flashcard_pack", "story", "grammar")


class AddonCreate(BaseModel):
    kind: str = Field(description="course, flashcard_pack, story, grammar")
    language_id: str
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="")
    source_url: str | None = None
    item_count: int | None = None


class AddonResponse(BaseModel):
    id: str
    kind: str
    language_id: str
    name: str
    description: str
    source_url: str | None = None
    author_id: str
    upvote_count: int = 0
    item_count: int | None = None
    status: str = "published"
    created_at: str
    updated_at: str


# ── Markdown storage (for rich content, React markdown editor compatibility) ──


class MarkdownStoreRequest(BaseModel):
    """Store markdown content. Key can be path-like (e.g. addons/abc123/readme)."""

    key: str = Field(min_length=1, max_length=500)
    content: str = Field(default="")
    content_type: str | None = None
    metadata: dict | None = None


class MarkdownResponse(BaseModel):
    key: str
    content: str
    content_type: str | None = None
    metadata: dict = Field(default_factory=dict)
    created_at: str
    updated_at: str
