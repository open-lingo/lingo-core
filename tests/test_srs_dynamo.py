"""Tests for the DynamoDB-backed SRS repository (FSRS-6 modal).

Exercises the cost item-8 write-if-newer path: the LWW merge must be
preserved exactly while dropping the per-card pre-read GetItems on the
common path. Uses a per-module ``ThreadedMotoServer`` (moto's in-process
mock returns sync bytes aiobotocore can't await).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import boto3
import pytest
import pytest_asyncio
from moto.server import ThreadedMotoServer

from app.db.dynamo import _session as dynamo_session
from app.db.dynamo.srs import DynamoSRSRepository

_REGION = "us-east-1"
_TABLE = "lingo_srs"
_USER = "user-1"


def _state(*, rec_review: str, prod_review: str, due: str = "2026-06-01", buried: str | None = None) -> dict[str, Any]:
    s: dict[str, Any] = {
        "recognition": {
            "stability": 2.5,
            "difficulty": 4.1,
            "state": "review",
            "interval": 3,
            "dueDate": due,
            "lastReviewDate": rec_review,
            "reps": 5,
            "lapses": 0,
        },
        "production": {
            "stability": 1.2,
            "difficulty": 5.0,
            "state": "learning",
            "interval": 1,
            "dueDate": due,
            "lastReviewDate": prod_review,
            "reps": 2,
            "lapses": 1,
        },
    }
    if buried is not None:
        s["buriedUntil"] = buried
    return s


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
            {"AttributeName": "user_id", "AttributeType": "S"},
            {"AttributeName": "dueDate", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "DueDate-Index",
                "KeySchema": [
                    {"AttributeName": "user_id", "KeyType": "HASH"},
                    {"AttributeName": "dueDate", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    client.get_waiter("table_exists").wait(TableName=_TABLE)


@pytest_asyncio.fixture()
async def repo(moto_server: str, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[DynamoSRSRepository]:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", _REGION)
    monkeypatch.setenv("AWS_ENDPOINT_URL_DYNAMODB", moto_server)

    monkeypatch.setattr(dynamo_session, "_session", None)
    monkeypatch.setattr(dynamo_session, "_resource_ctx", None)
    monkeypatch.setattr(dynamo_session, "_resource", None)

    _create_table(moto_server)
    repo = DynamoSRSRepository(_TABLE, _REGION)
    await repo.connect()
    try:
        yield repo
    finally:
        await dynamo_session.close_shared_resource()


async def test_first_write_lands_and_reads_back(repo: DynamoSRSRepository) -> None:
    state = _state(rec_review="2026-05-28", prod_review="2026-05-29")
    merged = await repo.upsert_cards(_USER, {"c1": state})
    assert merged["c1"]["recognition"]["lastReviewDate"] == "2026-05-28"
    got = await repo.get_card(_USER, "c1")
    assert got is not None
    assert got["production"]["lapses"] == 1


async def test_newer_review_wins(repo: DynamoSRSRepository) -> None:
    await repo.upsert_cards(_USER, {"c1": _state(rec_review="2026-05-28", prod_review="2026-05-28")})
    newer = _state(rec_review="2026-06-05", prod_review="2026-06-05", due="2026-06-10")
    merged = await repo.upsert_cards(_USER, {"c1": newer})
    assert merged["c1"]["recognition"]["lastReviewDate"] == "2026-06-05"
    got = await repo.get_card(_USER, "c1")
    assert got["recognition"]["lastReviewDate"] == "2026-06-05"
    assert got["recognition"]["dueDate"] == "2026-06-10"


async def test_stale_review_is_rejected(repo: DynamoSRSRepository) -> None:
    await repo.upsert_cards(_USER, {"c1": _state(rec_review="2026-05-28", prod_review="2026-05-28")})
    stale = _state(rec_review="2026-05-20", prod_review="2026-05-20", due="2026-05-21")
    merged = await repo.upsert_cards(_USER, {"c1": stale})
    # Server-existing (newer) state wins and is returned.
    assert merged["c1"]["recognition"]["lastReviewDate"] == "2026-05-28"
    got = await repo.get_card(_USER, "c1")
    assert got["recognition"]["lastReviewDate"] == "2026-05-28"


async def test_tie_keeps_server_state(repo: DynamoSRSRepository) -> None:
    await repo.upsert_cards(_USER, {"c1": _state(rec_review="2026-05-28", prod_review="2026-05-28")})
    # Same review marker, different (would-be-clobbering) core fields.
    tie = _state(rec_review="2026-05-28", prod_review="2026-05-28", due="2099-01-01")
    merged = await repo.upsert_cards(_USER, {"c1": tie})
    # Tie → server wins, the bogus far-future dueDate must not land.
    assert merged["c1"]["recognition"]["dueDate"] == "2026-06-01"


async def test_lastReviewedAt_timestamp_beats_same_day_date(repo: DynamoSRSRepository) -> None:
    # Seed with a bare-date marker.
    await repo.upsert_cards(_USER, {"c1": _state(rec_review="2026-05-28", prod_review="2026-05-28")})
    # Incoming carries a sub-day timestamp on the same day — must win.
    incoming = _state(rec_review="2026-05-28", prod_review="2026-05-28", due="2026-07-07")
    incoming["lastReviewedAt"] = "2026-05-28T10:00:00+00:00"
    merged = await repo.upsert_cards(_USER, {"c1": incoming})
    assert merged["c1"]["recognition"]["dueDate"] == "2026-07-07"


async def test_bury_change_lands_even_when_server_newer(repo: DynamoSRSRepository) -> None:
    await repo.upsert_cards(_USER, {"c1": _state(rec_review="2026-05-28", prod_review="2026-05-28")})
    # Older review, but a new bury — bury must land, core must stay server's.
    older_with_bury = _state(rec_review="2026-05-20", prod_review="2026-05-20", buried="2026-07-01")
    merged = await repo.upsert_cards(_USER, {"c1": older_with_bury})
    assert merged["c1"]["buriedUntil"] == "2026-07-01"
    assert merged["c1"]["recognition"]["lastReviewDate"] == "2026-05-28"
    got = await repo.get_card(_USER, "c1")
    assert got["buriedUntil"] == "2026-07-01"
    assert got["recognition"]["lastReviewDate"] == "2026-05-28"


async def test_due_index_reflects_min_modality(repo: DynamoSRSRepository) -> None:
    state = _state(rec_review="2026-05-28", prod_review="2026-05-29")
    state["recognition"]["dueDate"] = "2026-06-10"
    state["production"]["dueDate"] = "2026-06-02"
    await repo.upsert_cards(_USER, {"c1": state})
    due = await repo.get_due_cards(_USER, "2026-06-05")
    assert "c1" in due  # min(due) == 2026-06-02 <= 2026-06-05
    not_due = await repo.get_due_cards(_USER, "2026-06-01")
    assert "c1" not in not_due
