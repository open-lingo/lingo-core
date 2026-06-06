"""DynamoDB-backed admin audit repository tests.

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
from app.db.dynamo.audit import DynamoAuditRepository

_REGION = "us-east-1"
_TABLE = "lingo_admin_audit"


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
            {"AttributeName": "actor_id", "AttributeType": "S"},
            {"AttributeName": "target_kind", "AttributeType": "S"},
            {"AttributeName": "at", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "ActorIndex",
                "KeySchema": [
                    {"AttributeName": "actor_id", "KeyType": "HASH"},
                    {"AttributeName": "at", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                "IndexName": "TargetKindIndex",
                "KeySchema": [
                    {"AttributeName": "target_kind", "KeyType": "HASH"},
                    {"AttributeName": "at", "KeyType": "RANGE"},
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
) -> AsyncIterator[DynamoAuditRepository]:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", _REGION)
    monkeypatch.setenv("AWS_ENDPOINT_URL_DYNAMODB", moto_server)

    monkeypatch.setattr(dynamo_session, "_session", None)
    monkeypatch.setattr(dynamo_session, "_resource_ctx", None)
    monkeypatch.setattr(dynamo_session, "_resource", None)

    _create_table(moto_server)
    repo = DynamoAuditRepository(_TABLE, _REGION)
    await repo.connect()
    try:
        yield repo
    finally:
        await dynamo_session.close_shared_resource()


async def test_append_returns_populated_row(repo: DynamoAuditRepository) -> None:
    row = await repo.append(
        actor_id="actor-1",
        action="ban_user",
        target_id="target-1",
        target_kind="user",
        payload={"reason": "spam"},
    )
    assert row["id"]
    assert row["actor_id"] == "actor-1"
    assert row["action"] == "ban_user"
    assert row["target_id"] == "target-1"
    assert row["target_kind"] == "user"
    assert row["payload"] == {"reason": "spam"}
    assert row["at"]


async def test_append_persists_optional_target_id_as_sparse(repo: DynamoAuditRepository) -> None:
    # target_id=None should NOT be stored (sparse attribute) but should
    # round-trip as None on read.
    await repo.append(
        actor_id="actor-1",
        action="award_xp",
        target_id=None,
        target_kind="system",
        payload=None,
    )
    rows, _ = await repo.list()
    assert len(rows) == 1
    assert rows[0]["target_id"] is None
    assert rows[0]["payload"] == {}


async def test_list_is_newest_first(repo: DynamoAuditRepository) -> None:
    for i in range(3):
        await repo.append(
            actor_id="actor-1",
            action=f"action-{i}",
            target_id=f"t-{i}",
            target_kind="user",
        )
    rows, _ = await repo.list()
    actions = [r["action"] for r in rows]
    assert actions == ["action-2", "action-1", "action-0"]


async def test_list_filters_by_actor_id(repo: DynamoAuditRepository) -> None:
    await repo.append(actor_id="actor-A", action="a1", target_id="t1", target_kind="user")
    await repo.append(actor_id="actor-B", action="b1", target_id="t2", target_kind="user")
    await repo.append(actor_id="actor-A", action="a2", target_id="t3", target_kind="deck")

    rows, _ = await repo.list(actor_id="actor-A")
    actions = sorted(r["action"] for r in rows)
    assert actions == ["a1", "a2"]


async def test_list_filters_by_target_kind(repo: DynamoAuditRepository) -> None:
    await repo.append(actor_id="actor-1", action="a1", target_id="u1", target_kind="user")
    await repo.append(actor_id="actor-1", action="a2", target_id="d1", target_kind="deck")
    await repo.append(actor_id="actor-1", action="a3", target_id="d2", target_kind="deck")

    rows, _ = await repo.list(target_kind="deck")
    actions = sorted(r["action"] for r in rows)
    assert actions == ["a2", "a3"]


async def test_list_combined_actor_and_target_kind(repo: DynamoAuditRepository) -> None:
    await repo.append(actor_id="A", action="a1", target_id="t1", target_kind="user")
    await repo.append(actor_id="A", action="a2", target_id="t2", target_kind="deck")
    await repo.append(actor_id="B", action="b1", target_id="t3", target_kind="deck")

    rows, _ = await repo.list(actor_id="A", target_kind="deck")
    actions = [r["action"] for r in rows]
    assert actions == ["a2"]


async def test_list_pagination_with_cursor(repo: DynamoAuditRepository) -> None:
    for i in range(5):
        await repo.append(
            actor_id="actor-1",
            action=f"action-{i}",
            target_id=f"t-{i}",
            target_kind="user",
        )
    page1, cursor = await repo.list(limit=2)
    assert len(page1) == 2
    assert cursor is not None

    page2, cursor2 = await repo.list(limit=2, cursor=cursor)
    assert len(page2) == 2
    assert cursor2 is not None

    page3, cursor3 = await repo.list(limit=2, cursor=cursor2)
    assert len(page3) == 1
    assert cursor3 is None

    # No overlap across pages.
    seen = {r["id"] for r in page1 + page2 + page3}
    assert len(seen) == 5


async def test_payload_json_roundtrips_complex_types(repo: DynamoAuditRepository) -> None:
    payload = {"nested": {"k": [1, 2, 3]}, "flag": True, "name": "spam"}
    await repo.append(
        actor_id="actor-1",
        action="moderate",
        target_id="deck-1",
        target_kind="deck",
        payload=payload,
    )
    rows, _ = await repo.list()
    assert rows[0]["payload"] == payload
