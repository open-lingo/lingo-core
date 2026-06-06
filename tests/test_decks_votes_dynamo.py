"""Tests for the DynamoDB-backed deck-vote methods.

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
from app.db.dynamo.deck import DynamoDeckRepository

_REGION = "us-east-1"
_DECKS_TABLE = "lingo_decks"
_VOTES_TABLE = "lingo_deck_votes"


@pytest.fixture(scope="module")
def moto_server() -> Iterator[str]:
    server = ThreadedMotoServer(port=0)
    server.start()
    host, port = server.get_host_and_port()
    yield f"http://{host}:{port}"
    server.stop()


def _create_decks_table(client) -> None:
    try:
        client.delete_table(TableName=_DECKS_TABLE)
        client.get_waiter("table_not_exists").wait(TableName=_DECKS_TABLE)
    except client.exceptions.ResourceNotFoundException:
        pass
    client.create_table(
        TableName=_DECKS_TABLE,
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "authorId", "AttributeType": "S"},
            {"AttributeName": "authorUpdatedDeck", "AttributeType": "S"},
            {"AttributeName": "status", "AttributeType": "S"},
            {"AttributeName": "languageId", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "AuthorUpdated-Index",
                "KeySchema": [
                    {"AttributeName": "authorId", "KeyType": "HASH"},
                    {"AttributeName": "authorUpdatedDeck", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                "IndexName": "StatusLanguage-Index",
                "KeySchema": [
                    {"AttributeName": "status", "KeyType": "HASH"},
                    {"AttributeName": "languageId", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    client.get_waiter("table_exists").wait(TableName=_DECKS_TABLE)


def _create_votes_table(client) -> None:
    try:
        client.delete_table(TableName=_VOTES_TABLE)
        client.get_waiter("table_not_exists").wait(TableName=_VOTES_TABLE)
    except client.exceptions.ResourceNotFoundException:
        pass
    client.create_table(
        TableName=_VOTES_TABLE,
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
    client.get_waiter("table_exists").wait(TableName=_VOTES_TABLE)


def _create_tables(endpoint: str) -> None:
    client = boto3.client(
        "dynamodb",
        region_name=_REGION,
        endpoint_url=endpoint,
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    _create_decks_table(client)
    _create_votes_table(client)


@pytest_asyncio.fixture()
async def repo(
    moto_server: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[DynamoDeckRepository]:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", _REGION)
    monkeypatch.setenv("AWS_ENDPOINT_URL_DYNAMODB", moto_server)

    monkeypatch.setattr(dynamo_session, "_session", None)
    monkeypatch.setattr(dynamo_session, "_resource_ctx", None)
    monkeypatch.setattr(dynamo_session, "_resource", None)

    _create_tables(moto_server)
    repo = DynamoDeckRepository(
        _DECKS_TABLE, _REGION, votes_table_name=_VOTES_TABLE
    )
    await repo.connect()
    try:
        yield repo
    finally:
        await dynamo_session.close_shared_resource()


async def test_add_vote_then_state(repo: DynamoDeckRepository) -> None:
    await repo.add_vote("d1", "u1")
    state = await repo.get_vote_state("d1", "u1")
    assert state == {"count": 1, "voted": True}


async def test_add_vote_is_idempotent(repo: DynamoDeckRepository) -> None:
    await repo.add_vote("d1", "u1")
    await repo.add_vote("d1", "u1")
    assert await repo.get_vote_count("d1") == 1


async def test_remove_vote_clears_state(repo: DynamoDeckRepository) -> None:
    await repo.add_vote("d1", "u1")
    await repo.remove_vote("d1", "u1")
    state = await repo.get_vote_state("d1", "u1")
    assert state == {"count": 0, "voted": False}


async def test_remove_vote_unknown_is_noop(repo: DynamoDeckRepository) -> None:
    await repo.remove_vote("d1", "u1")
    assert await repo.get_vote_count("d1") == 0


async def test_get_vote_state_anonymous_user(repo: DynamoDeckRepository) -> None:
    await repo.add_vote("d1", "u1")
    state = await repo.get_vote_state("d1", None)
    assert state == {"count": 1, "voted": False}


async def test_get_vote_state_other_user(repo: DynamoDeckRepository) -> None:
    await repo.add_vote("d1", "u1")
    state = await repo.get_vote_state("d1", "u2")
    assert state == {"count": 1, "voted": False}


async def test_multiple_voters_count(repo: DynamoDeckRepository) -> None:
    for u in ("u1", "u2", "u3"):
        await repo.add_vote("d1", u)
    assert await repo.get_vote_count("d1") == 3


async def test_get_vote_counts_batch(repo: DynamoDeckRepository) -> None:
    await repo.add_vote("d1", "u1")
    await repo.add_vote("d1", "u2")
    await repo.add_vote("d2", "u1")
    counts = await repo.get_vote_counts(["d1", "d2", "d-empty"])
    assert counts == {"d1": 2, "d2": 1, "d-empty": 0}


async def test_get_vote_counts_empty_input(repo: DynamoDeckRepository) -> None:
    assert await repo.get_vote_counts([]) == {}
