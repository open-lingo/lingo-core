"""Tests for the DynamoDB-backed story repository.

Uses a per-module ``ThreadedMotoServer`` because moto's in-process mock
returns sync bytes that aiobotocore can't await (known incompatibility).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import boto3
import pytest
import pytest_asyncio
from moto.server import ThreadedMotoServer

from app.db.dynamo import _session as dynamo_session
from app.db.dynamo.story import DynamoStoryRepository

_REGION = "us-east-1"
_TABLE = "lingo_stories"


@pytest.fixture(scope="module")
def moto_server() -> Iterator[str]:
    server = ThreadedMotoServer(port=0)
    server.start()
    host, port = server.get_host_and_port()
    yield f"http://{host}:{port}"
    server.stop()


def _create_table(endpoint: str) -> None:
    client = boto3.client(
        "dynamodb",
        region_name=_REGION,
        endpoint_url=endpoint,
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    try:
        client.delete_table(TableName=_TABLE)
        client.get_waiter("table_not_exists").wait(TableName=_TABLE)
    except client.exceptions.ResourceNotFoundException:
        pass
    client.create_table(
        TableName=_TABLE,
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "language_id", "AttributeType": "S"},
            {"AttributeName": "status_updated_at", "AttributeType": "S"},
            {"AttributeName": "author_id", "AttributeType": "S"},
            {"AttributeName": "created_at", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "LanguageStatusIndex",
                "KeySchema": [
                    {"AttributeName": "language_id", "KeyType": "HASH"},
                    {"AttributeName": "status_updated_at", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                "IndexName": "AuthorIndex",
                "KeySchema": [
                    {"AttributeName": "author_id", "KeyType": "HASH"},
                    {"AttributeName": "created_at", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    client.get_waiter("table_exists").wait(TableName=_TABLE)


@pytest_asyncio.fixture()
async def repo(
    moto_server: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[DynamoStoryRepository]:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", _REGION)
    monkeypatch.setenv("AWS_ENDPOINT_URL_DYNAMODB", moto_server)

    monkeypatch.setattr(dynamo_session, "_session", None)
    monkeypatch.setattr(dynamo_session, "_resource_ctx", None)
    monkeypatch.setattr(dynamo_session, "_resource", None)

    _create_table(moto_server)
    repo = DynamoStoryRepository(_TABLE, _REGION)
    await repo.connect()
    try:
        yield repo
    finally:
        await dynamo_session.close_shared_resource()


async def test_create_and_get_story(repo: DynamoStoryRepository) -> None:
    await repo.create_story(
        "story-1",
        {
            "languageId": "ja",
            "title": "Hello",
            "description": "intro",
            "companionDeckId": "deck-1",
            "body": "# hi",
            "authorId": "user-1",
            "status": "draft",
        },
    )
    s = await repo.get_story("story-1")
    assert s is not None
    assert s["id"] == "story-1"
    assert s["languageId"] == "ja"
    assert s["title"] == "Hello"
    assert s["description"] == "intro"
    assert s["companionDeckId"] == "deck-1"
    assert s["body"] == "# hi"
    assert s["authorId"] == "user-1"
    assert s["status"] == "draft"
    assert s["createdAt"] is not None
    assert s["updatedAt"] is not None


async def test_get_missing_story_returns_none(repo: DynamoStoryRepository) -> None:
    assert await repo.get_story("nope") is None


async def test_update_story_patches_fields(repo: DynamoStoryRepository) -> None:
    await repo.create_story(
        "s",
        {"languageId": "ja", "title": "old", "authorId": "u1", "status": "draft"},
    )
    await repo.update_story("s", {"title": "new", "status": "published"})
    s = await repo.get_story("s")
    assert s is not None
    assert s["title"] == "new"
    assert s["status"] == "published"
    assert s["languageId"] == "ja"


async def test_update_missing_story_is_noop(repo: DynamoStoryRepository) -> None:
    await repo.update_story("ghost", {"title": "x"})
    assert await repo.get_story("ghost") is None


async def test_update_story_clears_description_on_explicit_none(
    repo: DynamoStoryRepository,
) -> None:
    await repo.create_story(
        "s",
        {"languageId": "ja", "title": "t", "description": "original", "authorId": "u1"},
    )
    await repo.update_story("s", {"description": None})
    s = await repo.get_story("s")
    assert s is not None
    assert s["description"] is None


async def test_delete_story_removes_it(repo: DynamoStoryRepository) -> None:
    await repo.create_story("s", {"languageId": "ja", "title": "t", "authorId": "u"})
    await repo.delete_story("s")
    assert await repo.get_story("s") is None


async def test_delete_missing_story_is_noop(repo: DynamoStoryRepository) -> None:
    # Should not raise.
    await repo.delete_story("ghost")


async def test_list_stories_no_filters_returns_all(repo: DynamoStoryRepository) -> None:
    for sid, lang, status in [
        ("a", "ja", "draft"),
        ("b", "ja", "published"),
        ("c", "ko", "draft"),
    ]:
        await repo.create_story(
            sid, {"languageId": lang, "title": sid, "status": status, "authorId": "u1"}
        )
    stories = await repo.list_stories()
    assert sorted(s["id"] for s in stories) == ["a", "b", "c"]


async def test_list_stories_filter_by_language(repo: DynamoStoryRepository) -> None:
    await repo.create_story(
        "a", {"languageId": "ja", "title": "a", "status": "draft", "authorId": "u1"}
    )
    await repo.create_story(
        "b", {"languageId": "ko", "title": "b", "status": "draft", "authorId": "u1"}
    )
    ja = await repo.list_stories(language_id="ja")
    assert [s["id"] for s in ja] == ["a"]


async def test_list_stories_filter_by_language_and_status(
    repo: DynamoStoryRepository,
) -> None:
    await repo.create_story(
        "draft", {"languageId": "ja", "title": "d", "status": "draft", "authorId": "u1"}
    )
    await repo.create_story(
        "pub", {"languageId": "ja", "title": "p", "status": "published", "authorId": "u1"}
    )
    pubs = await repo.list_stories(language_id="ja", status="published")
    assert [s["id"] for s in pubs] == ["pub"]


async def test_list_stories_filter_by_author(repo: DynamoStoryRepository) -> None:
    await repo.create_story("a", {"languageId": "ja", "title": "a", "authorId": "u1"})
    await repo.create_story("b", {"languageId": "ja", "title": "b", "authorId": "u2"})
    mine = await repo.list_stories(author_id="u1")
    assert [s["id"] for s in mine] == ["a"]


async def test_list_stories_filter_by_author_and_status(
    repo: DynamoStoryRepository,
) -> None:
    await repo.create_story(
        "a", {"languageId": "ja", "title": "a", "authorId": "u1", "status": "draft"}
    )
    await repo.create_story(
        "b", {"languageId": "ja", "title": "b", "authorId": "u1", "status": "published"}
    )
    drafts = await repo.list_stories(author_id="u1", status="draft")
    assert [s["id"] for s in drafts] == ["a"]


async def test_list_stories_filter_by_status_only(repo: DynamoStoryRepository) -> None:
    await repo.create_story(
        "a", {"languageId": "ja", "title": "a", "authorId": "u1", "status": "draft"}
    )
    await repo.create_story(
        "b", {"languageId": "ko", "title": "b", "authorId": "u2", "status": "published"}
    )
    drafts = await repo.list_stories(status="draft")
    assert [s["id"] for s in drafts] == ["a"]


async def test_list_stories_combined_filters_via_language_gsi(
    repo: DynamoStoryRepository,
) -> None:
    await repo.create_story(
        "a", {"languageId": "ja", "title": "a", "authorId": "u1", "status": "published"}
    )
    await repo.create_story(
        "b", {"languageId": "ja", "title": "b", "authorId": "u2", "status": "published"}
    )
    mine = await repo.list_stories(language_id="ja", author_id="u1", status="published")
    assert [s["id"] for s in mine] == ["a"]
