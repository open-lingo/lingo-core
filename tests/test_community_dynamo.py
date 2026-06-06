"""DynamoDB-backed community repository tests.

Uses a per-module ``ThreadedMotoServer`` because moto's in-process mock
returns sync bytes that aiobotocore can't await (known incompatibility).

Covers the full ``CommunityRepository`` protocol surface: category seeding,
tags, threads (+ category/tag/content filters, hot vs new sort), posts
(+ thread reply_count denorm + chronological listing), vote upsert/remove
with denormalised count recompute, addons (kind/author indexed list,
language/status client filters), markdown round-trip.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import boto3
import pytest
import pytest_asyncio
from moto.server import ThreadedMotoServer

from app.db.dynamo import _session as dynamo_session
from app.db.dynamo.community import DynamoCommunityRepository

_REGION = "us-east-1"
_THREADS = "lingo_community_threads"
_POSTS = "lingo_community_posts"
_VOTES = "lingo_community_votes"
_ADDONS = "lingo_community_addons"
_MARKDOWN = "lingo_community_markdown"


@pytest.fixture(scope="module")
def moto_server() -> Iterator[str]:
    server = ThreadedMotoServer(port=0)
    server.start()
    host, port = server.get_host_and_port()
    yield f"http://{host}:{port}"
    server.stop()


def _create_tables(endpoint: str) -> None:
    client = boto3.client(
        "dynamodb",
        region_name=_REGION,
        endpoint_url=endpoint,
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )

    def _drop(name: str) -> None:
        try:
            client.delete_table(TableName=name)
            client.get_waiter("table_not_exists").wait(TableName=name)
        except client.exceptions.ResourceNotFoundException:
            pass

    for name in (_THREADS, _POSTS, _VOTES, _ADDONS, _MARKDOWN):
        _drop(name)

    # threads — one GSI (CategoryUpdated-Index)
    client.create_table(
        TableName=_THREADS,
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "GSI1PK", "AttributeType": "S"},
            {"AttributeName": "GSI1SK", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "CategoryUpdated-Index",
                "KeySchema": [
                    {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    # posts — no GSIs
    client.create_table(
        TableName=_POSTS,
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    # votes — no GSIs
    client.create_table(
        TableName=_VOTES,
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    # addons — two GSIs (KindUpdated-Index, AuthorUpdated-Index)
    client.create_table(
        TableName=_ADDONS,
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "GSI1PK", "AttributeType": "S"},
            {"AttributeName": "GSI1SK", "AttributeType": "S"},
            {"AttributeName": "GSI2PK", "AttributeType": "S"},
            {"AttributeName": "GSI2SK", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "KindUpdated-Index",
                "KeySchema": [
                    {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                "IndexName": "AuthorUpdated-Index",
                "KeySchema": [
                    {"AttributeName": "GSI2PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI2SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    # markdown — no GSIs
    client.create_table(
        TableName=_MARKDOWN,
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    for name in (_THREADS, _POSTS, _VOTES, _ADDONS, _MARKDOWN):
        client.get_waiter("table_exists").wait(TableName=name)


@pytest_asyncio.fixture()
async def repo(
    moto_server: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[DynamoCommunityRepository]:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", _REGION)
    monkeypatch.setenv("AWS_ENDPOINT_URL_DYNAMODB", moto_server)

    monkeypatch.setattr(dynamo_session, "_session", None)
    monkeypatch.setattr(dynamo_session, "_resource_ctx", None)
    monkeypatch.setattr(dynamo_session, "_resource", None)

    _create_tables(moto_server)
    r = DynamoCommunityRepository(
        {
            "threads": _THREADS,
            "posts": _POSTS,
            "votes": _VOTES,
            "addons": _ADDONS,
            "markdown": _MARKDOWN,
        },
        _REGION,
    )
    await r.connect()
    try:
        yield r
    finally:
        await dynamo_session.close_shared_resource()


# ── Categories ───────────────────────────────────────────────────────────────


async def test_categories_seeded(repo: Any) -> None:
    cats = await repo.list_categories()
    slugs = {c["slug"] for c in cats}
    assert {"general", "features", "bugs", "tips", "content"} <= slugs
    # Sorted by sort_order
    assert cats == sorted(cats, key=lambda c: (c["sort_order"], c["id"]))


async def test_get_category_by_id_and_slug(repo: Any) -> None:
    by_id = await repo.get_category_by_id("c1")
    assert by_id is not None
    assert by_id["slug"] == "general"
    by_slug = await repo.get_category_by_slug("general")
    assert by_slug is not None
    assert by_slug["id"] == "c1"
    assert await repo.get_category_by_id("missing") is None
    assert await repo.get_category_by_slug("missing") is None


async def test_seed_categories_idempotent(repo: Any) -> None:
    # Connect twice → still 5 categories, no duplicates.
    await repo._seed_categories()
    cats = await repo.list_categories()
    assert len([c for c in cats if c["slug"] == "general"]) == 1


# ── Tags ─────────────────────────────────────────────────────────────────────


async def test_create_and_list_tags(repo: Any) -> None:
    t1 = await repo.create_tag({"slug": "korean", "name": "Korean"})
    t2 = await repo.create_tag({"slug": "japanese", "name": "Japanese", "color": "#abc"})

    fetched = await repo.get_tag_by_id(t1["id"])
    assert fetched is not None
    assert fetched["slug"] == "korean"

    tags = await repo.list_tags()
    names = [t["name"] for t in tags]
    # Sorted by name → Japanese before Korean
    assert names.index("Japanese") < names.index("Korean")

    # Color round-trips
    j = next(t for t in tags if t["id"] == t2["id"])
    assert j["color"] == "#abc"


# ── Threads ──────────────────────────────────────────────────────────────────


async def test_create_thread_round_trips(repo: Any) -> None:
    th = await repo.create_thread(
        {
            "category_id": "c1",
            "author_id": "u-alice",
            "author_name": "Alice",
            "title": "Hello world",
            "body_markdown": "**hi**",
        }
    )
    assert th["title"] == "Hello world"
    assert th["reply_count"] == 0
    assert th["upvote_count"] == 0
    assert th["is_pinned"] is False
    assert th["status"] == "open"
    assert th["excerpt"] == "Hello world"

    fetched = await repo.get_thread_by_id(th["id"])
    assert fetched is not None
    assert fetched["author_name"] == "Alice"


async def test_list_threads_by_category(repo: Any) -> None:
    a = await repo.create_thread({"category_id": "c1", "author_id": "u1", "title": "A"})
    b = await repo.create_thread({"category_id": "c2", "author_id": "u1", "title": "B"})

    in_c1 = await repo.list_threads(category_id="c1")
    in_c2 = await repo.list_threads(category_id="c2")
    assert any(t["id"] == a["id"] for t in in_c1)
    assert any(t["id"] == b["id"] for t in in_c2)
    assert not any(t["id"] == b["id"] for t in in_c1)


async def test_list_threads_by_tag(repo: Any) -> None:
    tag = await repo.create_tag({"slug": "help", "name": "help"})
    th = await repo.create_thread({"category_id": "c1", "author_id": "u1", "title": "Tagged"})
    await repo.set_thread_tags(th["id"], [tag["id"]])

    by_tag = await repo.list_threads(tag_id=tag["id"])
    assert [t["id"] for t in by_tag] == [th["id"]]
    # Returns [] for unknown tag
    assert await repo.list_threads(tag_id="nope") == []

    ids = await repo.get_thread_tag_ids(th["id"])
    assert ids == [tag["id"]]


async def test_set_thread_tags_diff(repo: Any) -> None:
    t_a = await repo.create_tag({"slug": "a", "name": "A"})
    t_b = await repo.create_tag({"slug": "b", "name": "B"})
    t_c = await repo.create_tag({"slug": "c", "name": "C"})
    th = await repo.create_thread({"category_id": "c1", "author_id": "u1", "title": "Diff me"})

    await repo.set_thread_tags(th["id"], [t_a["id"], t_b["id"]])
    assert set(await repo.get_thread_tag_ids(th["id"])) == {t_a["id"], t_b["id"]}

    await repo.set_thread_tags(th["id"], [t_b["id"], t_c["id"]])
    assert set(await repo.get_thread_tag_ids(th["id"])) == {t_b["id"], t_c["id"]}
    # Removed tag → no longer in reverse listing
    assert all(t["id"] != th["id"] for t in await repo.list_threads(tag_id=t_a["id"]))


async def test_list_threads_by_content(repo: Any) -> None:
    th = await repo.create_thread({"category_id": "c1", "author_id": "u1", "title": "Linked"})
    await repo.add_content_link(th["id"], "official_course", "official-ko", "ko")

    matched = await repo.list_threads(content_type="official_course", content_id="official-ko")
    assert [t["id"] for t in matched] == [th["id"]]

    via_helper = await repo.list_threads_by_content("official_course", "official-ko")
    assert [t["id"] for t in via_helper] == [th["id"]]

    links = await repo.list_content_links_by_thread(th["id"])
    assert len(links) == 1
    assert links[0]["content_type"] == "official_course"
    assert links[0]["content_id"] == "official-ko"
    assert links[0]["language_id"] == "ko"


async def test_list_threads_no_filter_returns_all_thread_rows(repo: Any) -> None:
    a = await repo.create_thread({"category_id": "c1", "author_id": "u1", "title": "A"})
    b = await repo.create_thread({"category_id": "c2", "author_id": "u1", "title": "B"})
    listed = await repo.list_threads(limit=100)
    ids = {t["id"] for t in listed}
    # Both threads come back regardless of category
    assert {a["id"], b["id"]} <= ids
    # No category/tag/content rows leak through
    assert all("category_id" in t for t in listed)


async def test_list_threads_hot_sort_pins_first(repo: Any) -> None:
    plain = await repo.create_thread({"category_id": "c1", "author_id": "u1", "title": "Plain"})
    pinned = await repo.create_thread(
        {"category_id": "c1", "author_id": "u1", "title": "Pinned", "is_pinned": True}
    )
    hot = await repo.list_threads(category_id="c1", sort="hot")
    ids = [t["id"] for t in hot]
    assert ids.index(pinned["id"]) < ids.index(plain["id"])


async def test_update_thread_patch_and_pin(repo: Any) -> None:
    th = await repo.create_thread({"category_id": "c1", "author_id": "u1", "title": "Edit me"})
    updated = await repo.update_thread(th["id"], {"title": "Edited", "is_pinned": True})
    assert updated["title"] == "Edited"
    assert updated["is_pinned"] is True
    assert updated["updated_at"] >= th["updated_at"]


async def test_update_thread_missing_raises(repo: Any) -> None:
    with pytest.raises(LookupError):
        await repo.update_thread("ghost", {"title": "x"})


async def test_increment_thread_views(repo: Any) -> None:
    th = await repo.create_thread({"category_id": "c1", "author_id": "u1", "title": "View me"})
    await repo.increment_thread_views(th["id"])
    await repo.increment_thread_views(th["id"])
    refreshed = await repo.get_thread_by_id(th["id"])
    assert refreshed["view_count"] == 2
    # Missing → silent no-op
    await repo.increment_thread_views("ghost")


# ── Posts ────────────────────────────────────────────────────────────────────


async def test_post_round_trip_and_reply_count(repo: Any) -> None:
    th = await repo.create_thread({"category_id": "c1", "author_id": "u-bob", "title": "Q"})
    before_updated = th["updated_at"]

    p1 = await repo.create_post({"thread_id": th["id"], "author_id": "u-carol", "body_markdown": "first"})
    p2 = await repo.create_post({"thread_id": th["id"], "author_id": "u-dave", "body_markdown": "second"})

    refreshed = await repo.get_thread_by_id(th["id"])
    assert refreshed["reply_count"] == 2
    assert refreshed["updated_at"] >= before_updated

    posts = await repo.list_posts_by_thread(th["id"])
    assert [p["id"] for p in posts] == [p1["id"], p2["id"]]

    fetched = await repo.get_post_by_id(p1["id"])
    assert fetched is not None
    assert fetched["body_markdown"] == "first"
    assert fetched["author_name"] == "User"


async def test_update_post_mirrors_both_rows(repo: Any) -> None:
    th = await repo.create_thread({"category_id": "c1", "author_id": "u1", "title": "T"})
    p = await repo.create_post({"thread_id": th["id"], "author_id": "u2", "body_markdown": "orig"})

    updated = await repo.update_post(p["id"], {"body_markdown": "new body"})
    assert updated["body_markdown"] == "new body"

    # Both the direct-lookup row and the chronological row reflect the edit.
    direct = await repo.get_post_by_id(p["id"])
    assert direct["body_markdown"] == "new body"
    posts = await repo.list_posts_by_thread(th["id"])
    assert posts[0]["body_markdown"] == "new body"


async def test_update_post_missing_raises(repo: Any) -> None:
    with pytest.raises(LookupError):
        await repo.update_post("ghost", {"body_markdown": "x"})


# ── Votes ────────────────────────────────────────────────────────────────────


async def test_thread_vote_recompute(repo: Any) -> None:
    th = await repo.create_thread({"category_id": "c1", "author_id": "u-x", "title": "Votable"})

    await repo.upsert_vote("u-a", "thread", th["id"], 1)
    await repo.upsert_vote("u-b", "thread", th["id"], 1)
    await repo.upsert_vote("u-c", "thread", th["id"], -1)
    refreshed = await repo.get_thread_by_id(th["id"])
    assert refreshed["upvote_count"] == 2
    assert refreshed["downvote_count"] == 1

    # Flip u-c → up
    await repo.upsert_vote("u-c", "thread", th["id"], 1)
    refreshed = await repo.get_thread_by_id(th["id"])
    assert refreshed["upvote_count"] == 3
    assert refreshed["downvote_count"] == 0

    # Remove u-a
    await repo.remove_vote("u-a", "thread", th["id"])
    refreshed = await repo.get_thread_by_id(th["id"])
    assert refreshed["upvote_count"] == 2

    assert await repo.get_user_vote("u-b", "thread", th["id"]) == 1
    assert await repo.get_user_vote("u-a", "thread", th["id"]) is None


async def test_post_votes_dont_bleed_into_thread(repo: Any) -> None:
    th = await repo.create_thread({"category_id": "c1", "author_id": "u-x", "title": "T"})
    p = await repo.create_post({"thread_id": th["id"], "author_id": "u-y", "body_markdown": "r"})

    await repo.upsert_vote("u-a", "thread", th["id"], 1)
    await repo.upsert_vote("u-a", "post", p["id"], 1)
    await repo.upsert_vote("u-b", "post", p["id"], 1)

    refreshed_post = await repo.get_post_by_id(p["id"])
    refreshed_thread = await repo.get_thread_by_id(th["id"])
    assert refreshed_post["upvote_count"] == 2
    assert refreshed_thread["upvote_count"] == 1


# ── Addons ───────────────────────────────────────────────────────────────────


async def test_list_addons_filters(repo: Any) -> None:
    a1 = await repo.create_addon(
        {"kind": "flashcard_pack", "language_id": "ko", "name": "K1", "author_id": "u-1"}
    )
    a2 = await repo.create_addon(
        {
            "kind": "flashcard_pack",
            "language_id": "ja",
            "name": "J1",
            "author_id": "u-2",
            "status": "published",
        }
    )
    a3 = await repo.create_addon(
        {"kind": "course", "language_id": "ko", "name": "K-course", "author_id": "u-1"}
    )

    by_kind = await repo.list_addons(kind="flashcard_pack")
    assert {a["id"] for a in by_kind} == {a1["id"], a2["id"]}

    by_lang = await repo.list_addons(language_id="ko")
    assert {a["id"] for a in by_lang} == {a1["id"], a3["id"]}

    by_status = await repo.list_addons(status="published")
    assert [a["id"] for a in by_status] == [a2["id"]]

    by_author = await repo.list_addons(author_id="u-1")
    assert {a["id"] for a in by_author} == {a1["id"], a3["id"]}


async def test_update_addon_round_trip(repo: Any) -> None:
    a = await repo.create_addon({"kind": "course", "name": "old", "author_id": "u-1"})
    updated = await repo.update_addon(a["id"], {"name": "new", "status": "published"})
    assert updated["name"] == "new"
    assert updated["status"] == "published"
    fetched = await repo.get_addon_by_id(a["id"])
    assert fetched["name"] == "new"


async def test_update_addon_missing_raises(repo: Any) -> None:
    with pytest.raises(LookupError):
        await repo.update_addon("ghost", {"name": "x"})


# ── Markdown KV ──────────────────────────────────────────────────────────────


async def test_markdown_upsert_and_delete(repo: Any) -> None:
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
    # created_at preserved across the upsert; updated_at advances.
    assert fetched["created_at"] == first["created_at"]

    deleted = await repo.delete_markdown("addons/abc/readme")
    assert deleted is True
    assert await repo.get_markdown("addons/abc/readme") is None
    assert await repo.delete_markdown("addons/abc/readme") is False


async def test_markdown_missing_returns_none(repo: Any) -> None:
    assert await repo.get_markdown("nope") is None
