"""In-memory mock community repository.

Simulates DB operations for development and when real DB is not yet connected.
Data resets on app restart. Compatible with React markdown editor (stores raw markdown).
"""

import uuid
from collections import defaultdict
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat()


class MockCommunityRepository:
    """In-memory implementation of CommunityRepository."""

    def __init__(self) -> None:
        self._categories: dict[str, dict] = {}
        self._tags: dict[str, dict] = {}
        self._threads: dict[str, dict] = {}
        self._posts: dict[str, dict] = {}
        self._thread_tags: dict[str, set[str]] = defaultdict(set)
        self._content_links: list[dict] = []
        self._votes: dict[tuple[str, str, str], int] = {}
        self._addons: dict[str, dict] = {}
        self._markdown: dict[str, dict] = {}
        self._seed_data()

    def _seed_data(self) -> None:
        """Seed initial categories, tags, and sample threads."""
        cats = [
            {"id": "c1", "slug": "general", "name_key": "forum.categoryGeneral",
             "description_key": "forum.categoryGeneralDesc", "sort_order": 0},
            {"id": "c2", "slug": "features", "name_key": "forum.categoryFeatures",
             "description_key": "forum.categoryFeaturesDesc", "sort_order": 1},
            {"id": "c3", "slug": "bugs", "name_key": "forum.categoryBugs",
             "description_key": "forum.categoryBugsDesc", "sort_order": 2},
            {"id": "c4", "slug": "tips", "name_key": "forum.categoryTips",
             "description_key": "forum.categoryTipsDesc", "sort_order": 3},
            {"id": "c5", "slug": "content", "name_key": "forum.categoryContent",
             "description_key": "forum.categoryContentDesc", "sort_order": 4},
        ]
        for c in cats:
            c["created_at"] = c["updated_at"] = _now()
            self._categories[c["id"]] = c

        tags = [
            {"id": "t1", "slug": "help", "name": "help"},
            {"id": "t2", "slug": "korean", "name": "Korean"},
            {"id": "t3", "slug": "japanese", "name": "Japanese"},
            {"id": "t4", "slug": "flashcards", "name": "flashcards"},
            {"id": "t5", "slug": "ux", "name": "UX"},
        ]
        for tg in tags:
            tg["created_at"] = _now()
            self._tags[tg["id"]] = tg

        # Sample thread
        thread = {
            "id": "th1",
            "category_id": "c1",
            "author_id": "u1",
            "author_name": "Community",
            "title": "Welcome to the Open Lingo community forum!",
            "excerpt": "Introduce yourself and share what you're learning.",
            "body_markdown": (
                "Welcome! This is the place to discuss, ask questions, and share tips.\n\n"
                "**Be kind and helpful.**"
            ),
            "reply_count": 0,
            "upvote_count": 0,
            "downvote_count": 0,
            "view_count": 0,
            "is_pinned": True,
            "status": "open",
            "created_at": _now(),
            "updated_at": _now(),
        }
        self._threads[thread["id"]] = thread
        self._thread_tags[thread["id"]] = set()

    # ── Categories ──

    async def list_categories(self) -> list[dict[str, Any]]:
        cats = sorted(self._categories.values(), key=lambda c: c.get("sort_order", 0))
        return [deepcopy(c) for c in cats]

    async def get_category_by_id(self, category_id: str) -> dict[str, Any] | None:
        c = self._categories.get(category_id)
        return deepcopy(c) if c else None

    async def get_category_by_slug(self, slug: str) -> dict[str, Any] | None:
        for c in self._categories.values():
            if c.get("slug") == slug:
                return deepcopy(c)
        return None

    # ── Tags ──

    async def list_tags(self) -> list[dict[str, Any]]:
        return [deepcopy(t) for t in self._tags.values()]

    async def get_tag_by_id(self, tag_id: str) -> dict[str, Any] | None:
        t = self._tags.get(tag_id)
        return deepcopy(t) if t else None

    async def create_tag(self, tag: dict[str, Any]) -> dict[str, Any]:
        tid = str(uuid.uuid4())
        row = {
            "id": tid,
            "slug": tag.get("slug", tid),
            "name": tag.get("name", ""),
            "created_at": _now(),
        }
        row.update(tag)
        self._tags[tid] = row
        return deepcopy(row)

    # ── Threads ──

    async def create_thread(self, thread: dict[str, Any]) -> dict[str, Any]:
        tid = str(uuid.uuid4())
        row = {
            "id": tid,
            "category_id": thread["category_id"],
            "author_id": thread["author_id"],
            "author_name": thread.get("author_name", "User"),
            "title": thread["title"],
            "excerpt": thread.get("excerpt", thread["title"][:200]),
            "body_markdown": thread.get("body_markdown", ""),
            "reply_count": 0,
            "upvote_count": 0,
            "downvote_count": 0,
            "view_count": 0,
            "is_pinned": thread.get("is_pinned", False),
            "status": thread.get("status", "open"),
            "created_at": _now(),
            "updated_at": _now(),
        }
        self._threads[tid] = row
        self._thread_tags[tid] = set()
        return deepcopy(row)

    async def get_thread_by_id(self, thread_id: str) -> dict[str, Any] | None:
        t = self._threads.get(thread_id)
        return deepcopy(t) if t else None

    async def list_threads(
        self,
        *,
        category_id: str | None = None,
        tag_id: str | None = None,
        content_type: str | None = None,
        content_id: str | None = None,
        sort: str = "hot",
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        items = list(self._threads.values())
        if category_id:
            items = [t for t in items if t["category_id"] == category_id]
        if tag_id:
            items = [t for t in items if tag_id in self._thread_tags.get(t["id"], set())]
        if content_type and content_id:
            linked = {
                cl["thread_id"]
                for cl in self._content_links
                if cl.get("content_type") == content_type and cl.get("content_id") == content_id
            }
            items = [t for t in items if t["id"] in linked]
        def _score(t: dict) -> int:
            return t["upvote_count"] - t["downvote_count"] + t["reply_count"] * 2

        if sort == "new":
            items.sort(key=lambda t: t["updated_at"], reverse=True)
        else:
            items.sort(key=lambda t: (-int(t.get("is_pinned", False)), -_score(t)))
        slice_ = items[offset : offset + limit]
        return [deepcopy(t) for t in slice_]

    async def update_thread(self, thread_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        t = self._threads.get(thread_id)
        if not t:
            raise LookupError(f"Thread {thread_id} not found")
        t.update(patch)
        t["updated_at"] = _now()
        return deepcopy(t)

    async def increment_thread_views(self, thread_id: str) -> None:
        t = self._threads.get(thread_id)
        if t:
            t["view_count"] = t.get("view_count", 0) + 1

    # ── Posts ──

    async def create_post(self, post: dict[str, Any]) -> dict[str, Any]:
        pid = str(uuid.uuid4())
        row = {
            "id": pid,
            "thread_id": post["thread_id"],
            "parent_id": post.get("parent_id"),
            "author_id": post["author_id"],
            "author_name": post.get("author_name", "User"),
            "body_markdown": post.get("body_markdown", ""),
            "upvote_count": 0,
            "downvote_count": 0,
            "created_at": _now(),
            "updated_at": _now(),
        }
        self._posts[pid] = row
        # Bump thread reply count
        tid = post["thread_id"]
        if tid in self._threads:
            self._threads[tid]["reply_count"] = self._threads[tid].get("reply_count", 0) + 1
            self._threads[tid]["updated_at"] = _now()
        return deepcopy(row)

    async def get_post_by_id(self, post_id: str) -> dict[str, Any] | None:
        p = self._posts.get(post_id)
        return deepcopy(p) if p else None

    async def list_posts_by_thread(
        self,
        thread_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        items = sorted(
            (p for p in self._posts.values() if p["thread_id"] == thread_id),
            key=lambda p: p["created_at"],
        )
        return [deepcopy(p) for p in items[offset : offset + limit]]

    async def update_post(self, post_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        p = self._posts.get(post_id)
        if not p:
            raise LookupError(f"Post {post_id} not found")
        p.update(patch)
        p["updated_at"] = _now()
        return deepcopy(p)

    # ── Thread tags ──

    async def set_thread_tags(self, thread_id: str, tag_ids: list[str]) -> None:
        self._thread_tags[thread_id] = set(tag_ids)

    async def get_thread_tag_ids(self, thread_id: str) -> list[str]:
        return list(self._thread_tags.get(thread_id, set()))

    # ── Content links ──

    async def add_content_link(
        self,
        thread_id: str,
        content_type: str,
        content_id: str,
        language_id: str | None = None,
    ) -> dict[str, Any]:
        link = {
            "id": str(uuid.uuid4()),
            "thread_id": thread_id,
            "content_type": content_type,
            "content_id": content_id,
            "language_id": language_id,
            "created_at": _now(),
        }
        self._content_links.append(link)
        return deepcopy(link)

    async def list_content_links_by_thread(self, thread_id: str) -> list[dict[str, Any]]:
        return [deepcopy(cl) for cl in self._content_links if cl["thread_id"] == thread_id]

    async def list_threads_by_content(
        self,
        content_type: str,
        content_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        linked = [
            cl["thread_id"]
            for cl in self._content_links
            if cl.get("content_type") == content_type and cl.get("content_id") == content_id
        ]
        items = [self._threads[tid] for tid in linked if tid in self._threads][:limit]
        return [deepcopy(t) for t in items]

    # ── Votes ──

    async def upsert_vote(
        self,
        user_id: str,
        target_type: str,
        target_id: str,
        value: int,
    ) -> None:
        key = (user_id, target_type, target_id)
        self._votes[key] = value
        # Update denormalized counts (simplified: we'd need to recalc from all votes in real impl)
        if target_type == "thread" and target_id in self._threads:
            t = self._threads[target_id]
            t["upvote_count"] = sum(
                1 for k, v in self._votes.items()
                if k[1] == "thread" and k[2] == target_id and v == 1
            )
            t["downvote_count"] = sum(
                1 for k, v in self._votes.items()
                if k[1] == "thread" and k[2] == target_id and v == -1
            )

    async def get_user_vote(
        self,
        user_id: str,
        target_type: str,
        target_id: str,
    ) -> int | None:
        return self._votes.get((user_id, target_type, target_id))

    async def remove_vote(
        self,
        user_id: str,
        target_type: str,
        target_id: str,
    ) -> None:
        self._votes.pop((user_id, target_type, target_id), None)

    # ── Addons ──

    async def create_addon(self, addon: dict[str, Any]) -> dict[str, Any]:
        aid = str(uuid.uuid4())
        row = {
            "id": aid,
            "kind": addon.get("kind", "course"),
            "language_id": addon.get("language_id", ""),
            "name": addon.get("name", ""),
            "description": addon.get("description", ""),
            "source_url": addon.get("source_url"),
            "author_id": addon["author_id"],
            "upvote_count": 0,
            "item_count": addon.get("item_count"),
            "status": addon.get("status", "draft"),
            "created_at": _now(),
            "updated_at": _now(),
        }
        self._addons[aid] = row
        return deepcopy(row)

    async def get_addon_by_id(self, addon_id: str) -> dict[str, Any] | None:
        a = self._addons.get(addon_id)
        return deepcopy(a) if a else None

    async def list_addons(
        self,
        *,
        kind: str | None = None,
        language_id: str | None = None,
        status: str | None = None,
        author_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        items = list(self._addons.values())
        if kind:
            items = [a for a in items if a.get("kind") == kind]
        if language_id:
            items = [a for a in items if a.get("language_id") == language_id]
        if status:
            items = [a for a in items if a.get("status") == status]
        if author_id:
            items = [a for a in items if a.get("author_id") == author_id]
        return [deepcopy(a) for a in items[offset : offset + limit]]

    async def update_addon(self, addon_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        a = self._addons.get(addon_id)
        if not a:
            raise LookupError(f"Addon {addon_id} not found")
        a.update(patch)
        a["updated_at"] = _now()
        return deepcopy(a)

    # ── Markdown file storage ──

    async def store_markdown(
        self,
        key: str,
        content: str,
        *,
        content_type: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        row = {
            "key": key,
            "content": content,
            "content_type": content_type,
            "metadata": metadata or {},
            "created_at": _now(),
            "updated_at": _now(),
        }
        self._markdown[key] = row
        return deepcopy(row)

    async def get_markdown(self, key: str) -> dict[str, Any] | None:
        m = self._markdown.get(key)
        return deepcopy(m) if m else None

    async def delete_markdown(self, key: str) -> bool:
        if key in self._markdown:
            del self._markdown[key]
            return True
        return False
