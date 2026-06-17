"""Tests for the DynamoDB-backed leaderboard read repository (cost item 5).

Proves the precomputed-table read path: bounded top-N Query on the
``XpRank-Index`` GSI + bounded rank/size COUNTs, replacing the old
``list_users`` Scan + per-user rollup fan-out.

The ``BucketXp-Index`` GSI reuses the table partition key (PK) as its hash and
``xp`` as its range, so the existing async writer already populates everything
the index needs — no writer change for the rank reads. The moto table mirrors
the infra definition (hash=PK, range=xp, KEYS_ONLY).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import boto3
import pytest
import pytest_asyncio
from moto.server import ThreadedMotoServer

from app.db.dynamo import _session as dynamo_session
from app.db.dynamo.leaderboard import DynamoLeaderboardRepository

_REGION = "us-east-1"
_TABLE = "lingo_social_leaderboard"
_BUCKET = "ja#2026-W21"


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
            {"AttributeName": "xp", "AttributeType": "N"},
        ],
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "BucketXp-Index",
                "KeySchema": [
                    {"AttributeName": "PK", "KeyType": "HASH"},
                    {"AttributeName": "xp", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "KEYS_ONLY"},
            },
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    client.get_waiter("table_exists").wait(TableName=_TABLE)


def _seed(endpoint: str, rows: list[tuple[str, int]]) -> None:
    """Write (user_id, xp) rows exactly as the async writer does — PK + SK + xp.
    The GSI hashes on PK + ranks on xp, so no extra attributes are needed."""
    res = boto3.resource(
        "dynamodb",
        region_name=_REGION,
        endpoint_url=endpoint,
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    table = res.Table(_TABLE)
    pk = f"BUCKET#{_BUCKET}"
    for user_id, xp in rows:
        table.put_item(Item={"PK": pk, "SK": f"USER#{user_id}", "xp": xp})


@pytest_asyncio.fixture()
async def repo(moto_server: str, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[DynamoLeaderboardRepository]:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", _REGION)
    monkeypatch.setenv("AWS_ENDPOINT_URL_DYNAMODB", moto_server)

    monkeypatch.setattr(dynamo_session, "_session", None)
    monkeypatch.setattr(dynamo_session, "_resource_ctx", None)
    monkeypatch.setattr(dynamo_session, "_resource", None)

    _create_table(moto_server)
    _seed(
        moto_server,
        [("alice", 300), ("bob", 500), ("carol", 100), ("dave", 50)],
    )
    repo = DynamoLeaderboardRepository(_TABLE, _REGION)
    await repo.connect()
    try:
        yield repo
    finally:
        await dynamo_session.close_shared_resource()


async def test_top_n_sorted_desc(repo: DynamoLeaderboardRepository) -> None:
    rows = await repo.top_n(_BUCKET, 3)
    assert [r["user_id"] for r in rows] == ["bob", "alice", "carol"]
    assert rows[0]["xp"] == 500


async def test_top_n_respects_limit(repo: DynamoLeaderboardRepository) -> None:
    rows = await repo.top_n(_BUCKET, 2)
    assert len(rows) == 2
    assert rows[0]["user_id"] == "bob"


async def test_get_entry(repo: DynamoLeaderboardRepository) -> None:
    entry = await repo.get_entry(_BUCKET, "carol")
    assert entry == {"user_id": "carol", "xp": 100}
    assert await repo.get_entry(_BUCKET, "ghost") is None


async def test_rank_for_xp(repo: DynamoLeaderboardRepository) -> None:
    # bob (500) is rank 1; alice (300) rank 2; carol (100) rank 3.
    assert await repo.rank_for_xp(_BUCKET, 500) == 1
    assert await repo.rank_for_xp(_BUCKET, 300) == 2
    assert await repo.rank_for_xp(_BUCKET, 100) == 3


async def test_bucket_size(repo: DynamoLeaderboardRepository) -> None:
    assert await repo.bucket_size(_BUCKET) == 4


async def test_empty_bucket_degrades(repo: DynamoLeaderboardRepository) -> None:
    assert await repo.top_n("ko#2099-W01", 10) == []
    assert await repo.bucket_size("ko#2099-W01") == 0
    assert await repo.get_entry("ko#2099-W01", "alice") is None
