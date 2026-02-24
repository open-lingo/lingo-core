"""Constants for moderation and content lifecycle.

See docs/MODERATION_DESIGN.md for the full model.
"""

# Content status (stories, decks). Current: draft, published. Planned: full lifecycle.
CONTENT_STATUS_DRAFT = "draft"
CONTENT_STATUS_SUBMITTED = "submitted"
CONTENT_STATUS_UNDER_REVIEW = "under_review"
CONTENT_STATUS_PUBLISHED = "published"
CONTENT_STATUS_CHANGES_REQUESTED = "changes_requested"
CONTENT_STATUS_REJECTED = "rejected"
CONTENT_STATUS_ARCHIVED = "archived"
CONTENT_STATUS_REMOVED = "removed"

CONTENT_STATUSES = frozenset({
    CONTENT_STATUS_DRAFT,
    CONTENT_STATUS_SUBMITTED,
    CONTENT_STATUS_UNDER_REVIEW,
    CONTENT_STATUS_PUBLISHED,
    CONTENT_STATUS_CHANGES_REQUESTED,
    CONTENT_STATUS_REJECTED,
    CONTENT_STATUS_ARCHIVED,
    CONTENT_STATUS_REMOVED,
})

# User account status. Current: active, banned. Planned: muted, suspended, deleted.
USER_STATUS_ACTIVE = "active"
USER_STATUS_MUTED = "muted"
USER_STATUS_SUSPENDED = "suspended"
USER_STATUS_BANNED = "banned"
USER_STATUS_DELETED = "deleted"

USER_STATUSES = frozenset({
    USER_STATUS_ACTIVE,
    USER_STATUS_MUTED,
    USER_STATUS_SUSPENDED,
    USER_STATUS_BANNED,
    USER_STATUS_DELETED,
})

# Report target types
REPORT_TARGET_STORY = "story"
REPORT_TARGET_DECK = "deck"
REPORT_TARGET_POST = "post"
REPORT_TARGET_USER = "user"

REPORT_TARGET_TYPES = frozenset({
    REPORT_TARGET_STORY,
    REPORT_TARGET_DECK,
    REPORT_TARGET_POST,
    REPORT_TARGET_USER,
})

# Report status
REPORT_STATUS_OPEN = "open"
REPORT_STATUS_RESOLVED = "resolved"
REPORT_STATUS_DISMISSED = "dismissed"
