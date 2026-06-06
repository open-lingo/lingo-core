"""Tests for the DynamoDB-backed tag repository.

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
from app.db.dynamo.tag import DynamoTagRepository

_REGION = "us-east-1"
_TABLE = "lingo_tags"


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
            {"AttributeName": "GSI1PK", "AttributeType": "S"},
            {"AttributeName": "GSI1SK", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "TagDeck-Index",
                "KeySchema": [
                    {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
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
) -> AsyncIterator[DynamoTagRepository]:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", _REGION)
    monkeypatch.setenv("AWS_ENDPOINT_URL_DYNAMODB", moto_server)

    monkeypatch.setattr(dynamo_session, "_session", None)
    monkeypatch.setattr(dynamo_session, "_resource_ctx", None)
    monkeypatch.setattr(dynamo_session, "_resource", None)

    _create_table(moto_server)
    repo = DynamoTagRepository(_TABLE, _REGION)
    await repo.connect()
    try:
        yield repo
    finally:
        await dynamo_session.close_shared_resource()


async def test_create_and_get_tag(repo: DynamoTagRepository) -> None:
    created = await repo.create_tag(
        "jlpt-n5", "JLPT N5", description="Beginner Japanese", color="#abc"
    )
    assert created["slug"] == "jlpt-n5"
    fetched = await repo.get_tag("jlpt-n5")
    assert fetched is not None
    assert fetched["display_name"] == "JLPT N5"
    assert fetched["description"] == "Beginner Japanese"
    assert fetched["color"] == "#abc"
    assert fetched["created_at"] is not None


async def test_create_tag_duplicate_raises(repo: DynamoTagRepository) -> None:
    await repo.create_tag("x", "X")
    with pytest.raises(ValueError):
        await repo.create_tag("x", "X again")


async def test_get_tag_missing_returns_none(repo: DynamoTagRepository) -> None:
    assert await repo.get_tag("ghost") is None


async def test_list_tags_sorted(repo: DynamoTagRepository) -> None:
    await repo.create_tag("b", "B")
    await repo.create_tag("a", "A")
    await repo.create_tag("c", "C")
    tags = await repo.list_tags()
    assert [t["slug"] for t in tags] == ["a", "b", "c"]


async def test_update_tag_patches_only_supplied(repo: DynamoTagRepository) -> None:
    await repo.create_tag("x", "Old name", description="orig", color="#111")
    updated = await repo.update_tag("x", display_name="New name")
    assert updated is not None
    assert updated["display_name"] == "New name"
    assert updated["description"] == "orig"
    assert updated["color"] == "#111"


async def test_update_tag_missing_returns_none(repo: DynamoTagRepository) -> None:
    assert await repo.update_tag("ghost", display_name="x") is None


async def test_delete_tag_removes_and_cascades(repo: DynamoTagRepository) -> None:
    await repo.create_tag("x", "X")
    await repo.set_deck_tags("deck-1", ["x"])
    await repo.set_deck_tags("deck-2", ["x"])
    assert await repo.delete_tag("x") is True
    assert await repo.get_tag("x") is None
    # Mirror rows should be gone too.
    assert await repo.list_decks_for_tag("x") == []
    assert await repo.list_tags_for_deck("deck-1") == []
    assert await repo.list_tags_for_deck("deck-2") == []


async def test_delete_missing_tag_returns_false(repo: DynamoTagRepository) -> None:
    assert await repo.delete_tag("ghost") is False


async def test_set_deck_tags_creates_mirror_rows(repo: DynamoTagRepository) -> None:
    await repo.create_tag("a", "A")
    await repo.create_tag("b", "B")
    await repo.set_deck_tags("deck-1", ["a", "b"])
    assert await repo.list_tags_for_deck("deck-1") == ["a", "b"]
    assert await repo.list_decks_for_tag("a") == ["deck-1"]
    assert await repo.list_decks_for_tag("b") == ["deck-1"]


async def test_set_deck_tags_diffs_correctly(repo: DynamoTagRepository) -> None:
    for s in ("a", "b", "c"):
        await repo.create_tag(s, s.upper())
    await repo.set_deck_tags("deck-1", ["a", "b"])
    await repo.set_deck_tags("deck-1", ["b", "c"])
    assert await repo.list_tags_for_deck("deck-1") == ["b", "c"]
    # Removed tag's reverse lookup should be empty.
    assert await repo.list_decks_for_tag("a") == []
    assert await repo.list_decks_for_tag("b") == ["deck-1"]
    assert await repo.list_decks_for_tag("c") == ["deck-1"]


async def test_set_deck_tags_dedups_input(repo: DynamoTagRepository) -> None:
    await repo.create_tag("a", "A")
    await repo.set_deck_tags("deck-1", ["a", "a", "a"])
    assert await repo.list_tags_for_deck("deck-1") == ["a"]


async def test_set_deck_tags_clears_with_empty_list(repo: DynamoTagRepository) -> None:
    await repo.create_tag("a", "A")
    await repo.set_deck_tags("deck-1", ["a"])
    await repo.set_deck_tags("deck-1", [])
    assert await repo.list_tags_for_deck("deck-1") == []


async def test_set_deck_tags_unknown_slug_raises(repo: DynamoTagRepository) -> None:
    with pytest.raises(ValueError):
        await repo.set_deck_tags("deck-1", ["nope"])


async def test_list_tags_for_decks_bulk(repo: DynamoTagRepository) -> None:
    for s in ("a", "b"):
        await repo.create_tag(s, s.upper())
    await repo.set_deck_tags("deck-1", ["a"])
    await repo.set_deck_tags("deck-2", ["a", "b"])
    out = await repo.list_tags_for_decks(["deck-1", "deck-2", "deck-empty"])
    assert out == {
        "deck-1": ["a"],
        "deck-2": ["a", "b"],
        "deck-empty": [],
    }


async def test_list_tags_for_decks_empty_input(repo: DynamoTagRepository) -> None:
    assert await repo.list_tags_for_decks([]) == {}
