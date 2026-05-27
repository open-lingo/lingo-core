"""Community persistence + API tests.

Exercises ``SqliteCommunityRepository`` directly against a temp DB and the
HTTP surface end-to-end with the FastAPI ``TestClient``. The two layers cover
different concerns: repo tests check the schema, vote recomputation, and
markdown KV upsert; API tests prove the router actually wires through to the
new SQLite repo (no stub leakage).
"""

from __future__ import annotations

import os
import tempfile
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

# ── Direct-repo fixtures ─────────────────────────────────────────────────────


@pytest.fixture()
def tmp_db_path() -> Iterator[str]:
    fd, path = tempfile.mkstemp(suffix=".db", prefix="lingo-test-community-")
    os.close(fd)
    try:
        yield path
    finally:
        try:
            Path(path).unlink()
        except FileNotFoundError:
            pass


@pytest_asyncio.fixture()
async def repo(tmp_db_path: str) -> AsyncIterator:
    from app.db.sqlite.community import SqliteCommunityRepository

    r = SqliteCommunityRepository(tmp_db_path)
    await r.connect()
    try:
        yield r
    finally:
        await r.close()


# ── Repo-level tests ─────────────────────────────────────────────────────────


async def test_default_categories_seeded(repo: Any) -> None:
    """connect() must seed the 5 default forum categories the React UI expects."""
    cats = await repo.list_categories()
    slugs = {c["slug"] for c in cats}
    assert {"general", "features", "bugs", "tips", "content"} <= slugs
    # Ordering by sort_order
    assert cats == sorted(cats, key=lambda c: c["sort_order"])


async def test_create_and_list_thread(repo: Any) -> None:
    """Threads round-trip through create/list/get with tag and content link denorm."""
    tag = await repo.create_tag({"slug": "korean", "name": "Korean"})
    thread = await repo.create_thread(
        {
            "category_id": "c1",
            "author_id": "u-alice",
            "author_name": "Alice",
            "title": "Hello world",
            "body_markdown": "**hi**",
        }
    )
    await repo.set_thread_tags(thread["id"], [tag["id"]])
    await repo.add_content_link(thread["id"], "official_course", "official-ko", "ko")

    assert thread["title"] == "Hello world"
    assert thread["reply_count"] == 0
    assert thread["upvote_count"] == 0
    assert thread["is_pinned"] is False

    # list_threads finds it under the right category
    listed = await repo.list_threads(category_id="c1")
    assert any(t["id"] == thread["id"] for t in listed)

    # tag + content link filters work
    by_tag = await repo.list_threads(tag_id=tag["id"])
    assert [t["id"] for t in by_tag] == [thread["id"]]
    by_content = await repo.list_threads(content_type="official_course", content_id="official-ko")
    assert [t["id"] for t in by_content] == [thread["id"]]

    # get_thread_tag_ids round-trips
    tag_ids = await repo.get_thread_tag_ids(thread["id"])
    assert tag_ids == [tag["id"]]


async def test_post_increments_reply_count(repo: Any) -> None:
    """create_post must bump the parent thread's reply_count and updated_at."""
    thread = await repo.create_thread({"category_id": "c1", "author_id": "u-bob", "title": "Question"})
    before_updated = thread["updated_at"]

    post = await repo.create_post({"thread_id": thread["id"], "author_id": "u-carol", "body_markdown": "Try X."})
    posts = await repo.list_posts_by_thread(thread["id"])
    refreshed = await repo.get_thread_by_id(thread["id"])

    assert post["thread_id"] == thread["id"]
    assert len(posts) == 1
    assert posts[0]["id"] == post["id"]
    assert refreshed["reply_count"] == 1
    assert refreshed["updated_at"] >= before_updated


async def test_thread_and_post_votes_recompute(repo: Any) -> None:
    """upsert_vote must recompute denormalized counts for both target kinds."""
    thread = await repo.create_thread({"category_id": "c1", "author_id": "u-x", "title": "Votable"})
    post = await repo.create_post({"thread_id": thread["id"], "author_id": "u-y", "body_markdown": "reply"})

    # Two upvotes on the thread, one downvote
    await repo.upsert_vote("u-a", "thread", thread["id"], 1)
    await repo.upsert_vote("u-b", "thread", thread["id"], 1)
    await repo.upsert_vote("u-c", "thread", thread["id"], -1)
    refreshed = await repo.get_thread_by_id(thread["id"])
    assert refreshed["upvote_count"] == 2
    assert refreshed["downvote_count"] == 1

    # Flip u-c's vote to up
    await repo.upsert_vote("u-c", "thread", thread["id"], 1)
    refreshed = await repo.get_thread_by_id(thread["id"])
    assert refreshed["upvote_count"] == 3
    assert refreshed["downvote_count"] == 0

    # Remove u-a — count drops back to 2
    await repo.remove_vote("u-a", "thread", thread["id"])
    refreshed = await repo.get_thread_by_id(thread["id"])
    assert refreshed["upvote_count"] == 2

    # Post votes use a different target_kind and don't bleed into thread counts
    await repo.upsert_vote("u-a", "post", post["id"], 1)
    refreshed_post = await repo.get_post_by_id(post["id"])
    assert refreshed_post["upvote_count"] == 1
    refreshed = await repo.get_thread_by_id(thread["id"])
    assert refreshed["upvote_count"] == 2  # unchanged

    # get_user_vote returns the right value or None
    assert await repo.get_user_vote("u-a", "post", post["id"]) == 1
    assert await repo.get_user_vote("u-nobody", "post", post["id"]) is None


async def test_markdown_upsert_by_key(repo: Any) -> None:
    """store_markdown is idempotent on key — second call updates content."""
    first = await repo.store_markdown(
        "addons/abc/readme",
        "# Hello",
        content_type="text/markdown",
        metadata={"version": 1},
    )
    second = await repo.store_markdown(
        "addons/abc/readme",
        "# Hello world",
        content_type="text/markdown",
        metadata={"version": 2},
    )
    fetched = await repo.get_markdown("addons/abc/readme")

    assert first["content"] == "# Hello"
    assert second["content"] == "# Hello world"
    assert fetched["content"] == "# Hello world"
    assert fetched["metadata"] == {"version": 2}

    deleted = await repo.delete_markdown("addons/abc/readme")
    assert deleted is True
    assert await repo.get_markdown("addons/abc/readme") is None
    # Deleting again returns False (not an error)
    assert await repo.delete_markdown("addons/abc/readme") is False


async def test_list_addons_filters(repo: Any) -> None:
    """list_addons supports kind/language_id/status/author_id filters."""
    a1 = await repo.create_addon({"kind": "flashcard_pack", "language_id": "ko", "name": "K1", "author_id": "u-1"})
    a2 = await repo.create_addon({"kind": "flashcard_pack", "language_id": "ja", "name": "J1", "author_id": "u-2", "status": "published"})
    a3 = await repo.create_addon({"kind": "course", "language_id": "ko", "name": "K-course", "author_id": "u-1"})

    by_kind = await repo.list_addons(kind="flashcard_pack")
    assert {a["id"] for a in by_kind} == {a1["id"], a2["id"]}

    by_lang = await repo.list_addons(language_id="ko")
    assert {a["id"] for a in by_lang} == {a1["id"], a3["id"]}

    by_status = await repo.list_addons(status="published")
    assert [a["id"] for a in by_status] == [a2["id"]]

    by_author = await repo.list_addons(author_id="u-1")
    assert {a["id"] for a in by_author} == {a1["id"], a3["id"]}


# ── End-to-end HTTP fixtures ─────────────────────────────────────────────────


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    """Boot the full FastAPI app on a temp SQLite DB with DEBUG bypass."""
    tmp_db = os.path.join(tempfile.mkdtemp(prefix="lingo-community-api-"), "community.db")
    os.environ["DB_BACKEND"] = "sqlite"
    os.environ["SQLITE_PATH"] = tmp_db
    os.environ["DEBUG"] = "true"
    os.environ["DEV_USER"] = "auth0|alice"

    import importlib

    from app import config as config_mod

    importlib.reload(config_mod)
    from app.db import provider as provider_mod

    importlib.reload(provider_mod)
    from app.auth import dependencies as auth_dep_mod

    importlib.reload(auth_dep_mod)
    from app import main as main_mod

    importlib.reload(main_mod)

    with TestClient(main_mod.app) as c:
        # Register the dev user
        c.post(
            "/api/core/v1/users/me",
            json={"username": f"u{uuid.uuid4().hex[:6]}", "display_name": "Alice"},
        )
        yield c


def test_api_categories_seeded(client: TestClient) -> None:
    """GET /community/categories returns the seeded defaults via SQLite."""
    resp = client.get("/api/core/v1/community/categories")
    assert resp.status_code == 200, resp.text
    slugs = {c["slug"] for c in resp.json()}
    assert {"general", "features", "bugs", "tips", "content"} <= slugs
