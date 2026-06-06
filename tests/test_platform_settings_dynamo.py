"""DynamoDB-backed platform settings repository tests.

Uses a per-module ``ThreadedMotoServer`` because moto's in-process mock
returns sync bytes that aiobotocore can't await (known incompatibility).
The threaded server serves real HTTP, and we point aioboto3 at it via
``AWS_ENDPOINT_URL_DYNAMODB``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import boto3
import pytest
import pytest_asyncio
from moto.server import ThreadedMotoServer

from app.db.dynamo import _session as dynamo_session
from app.db.dynamo.platform_settings import DynamoPlatformSettingsRepository

_REGION = "us-east-1"
_TABLE = "lingo_platform_settings"


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
    # Tests share the module-scoped moto server, so reset state between tests.
    try:
        client.delete_table(TableName=_TABLE)
        client.get_waiter("table_not_exists").wait(TableName=_TABLE)
    except client.exceptions.ResourceNotFoundException:
        pass
    client.create_table(
        TableName=_TABLE,
        AttributeDefinitions=[
            {"AttributeName": "key", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "key", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    client.get_waiter("table_exists").wait(TableName=_TABLE)


@pytest_asyncio.fixture()
async def repo(
    moto_server: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[DynamoPlatformSettingsRepository]:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", _REGION)
    monkeypatch.setenv("AWS_ENDPOINT_URL_DYNAMODB", moto_server)

    # Reset shared-resource singleton so this test owns its own client.
    monkeypatch.setattr(dynamo_session, "_session", None)
    monkeypatch.setattr(dynamo_session, "_resource_ctx", None)
    monkeypatch.setattr(dynamo_session, "_resource", None)

    _create_table(moto_server)
    repo = DynamoPlatformSettingsRepository(_TABLE, _REGION)
    await repo.connect()
    try:
        yield repo
    finally:
        await dynamo_session.close_shared_resource()


async def test_get_missing_returns_none(repo: DynamoPlatformSettingsRepository) -> None:
    assert await repo.get("xp_economy") is None


async def test_put_then_get_roundtrips(repo: DynamoPlatformSettingsRepository) -> None:
    value = {"lesson_pass_xp": 25, "review_xp": 5}
    stored = await repo.put("xp_economy", value)
    assert stored == value

    got = await repo.get("xp_economy")
    assert got == value


async def test_put_overwrites_existing(repo: DynamoPlatformSettingsRepository) -> None:
    await repo.put("xp_economy", {"lesson_pass_xp": 10})
    await repo.put("xp_economy", {"lesson_pass_xp": 50, "review_xp": 7})

    got = await repo.get("xp_economy")
    assert got == {"lesson_pass_xp": 50, "review_xp": 7}


async def test_put_preserves_insertion_order(repo: DynamoPlatformSettingsRepository) -> None:
    # Dict iteration order matters to the FE; JSON-encoded storage keeps it.
    value = {"z_last": 1, "a_first": 2, "m_middle": 3}
    await repo.put("ordered", value)
    got = await repo.get("ordered")
    assert list(got) == ["z_last", "a_first", "m_middle"]


async def test_distinct_keys_are_independent(repo: DynamoPlatformSettingsRepository) -> None:
    await repo.put("xp_economy", {"a": 1})
    await repo.put("flags", {"b": 2})

    assert await repo.get("xp_economy") == {"a": 1}
    assert await repo.get("flags") == {"b": 2}
