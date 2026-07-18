"""Tests for the DynamoDB-backed progress repository (cost item 6).

Covers the reduced-op write path: conditional-UpdateItem lesson rollup,
ReturnValues day rollup, and put_attempt idempotency (dropped the leading
GetItem; relies on the TransactWrite conditional guard). Uses a per-module
``ThreadedMotoServer`` (moto's in-process mock returns sync bytes aiobotocore
can't await).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import boto3
import pytest
import pytest_asyncio
from botocore.exceptions import ClientError
from moto.server import ThreadedMotoServer

from app.db.dynamo import _session as dynamo_session
from app.db.dynamo import progress as progress_module
from app.db.dynamo.progress import DynamoProgressRepository

_REGION = "us-east-1"
_TABLE = "lingo_progress"
_USER = "user-1"


def _attempt(*, cid: str, lesson: str, ts: str, score: float, passed: bool) -> dict[str, Any]:
    return {
        "attemptId": f"a-{cid}",
        "clientAttemptId": cid,
        "lessonId": lesson,
        "attemptedAt": ts,
        "durationSec": 120,
        "passed": passed,
        "score": score,
        "steps": [],
    }


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
            {"AttributeName": "attemptedAt", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "UserAttempts-Index",
                "KeySchema": [
                    {"AttributeName": "user_id", "KeyType": "HASH"},
                    {"AttributeName": "attemptedAt", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    client.get_waiter("table_exists").wait(TableName=_TABLE)


@pytest_asyncio.fixture()
async def repo(moto_server: str, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[DynamoProgressRepository]:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", _REGION)
    monkeypatch.setenv("AWS_ENDPOINT_URL_DYNAMODB", moto_server)

    monkeypatch.setattr(dynamo_session, "_session", None)
    monkeypatch.setattr(dynamo_session, "_resource_ctx", None)
    monkeypatch.setattr(dynamo_session, "_resource", None)

    _create_table(moto_server)
    repo = DynamoProgressRepository(_TABLE, _REGION)
    await repo.connect()
    try:
        yield repo
    finally:
        await dynamo_session.close_shared_resource()


# ── Lesson rollup ─────────────────────────────────────────────────────────────


async def test_lesson_rollup_create(repo: DynamoProgressRepository) -> None:
    out = await repo.update_lesson_rollup(
        _USER, "l1", _attempt(cid="c1", lesson="l1", ts="2026-06-01T00:00:00Z", score=0.8, passed=True)
    )
    assert out["lessonId"] == "l1"
    assert out["bestScore"] == 0.8
    assert out["attemptCount"] == 1
    assert out["firstPassedAt"] == "2026-06-01T00:00:00Z"


async def test_lesson_rollup_raises_best_and_counts(repo: DynamoProgressRepository) -> None:
    await repo.update_lesson_rollup(
        _USER, "l2", _attempt(cid="c1", lesson="l2", ts="2026-06-01T00:00:00Z", score=0.5, passed=False)
    )
    out = await repo.update_lesson_rollup(
        _USER, "l2", _attempt(cid="c2", lesson="l2", ts="2026-06-02T00:00:00Z", score=0.9, passed=True)
    )
    assert out["bestScore"] == 0.9
    assert out["attemptCount"] == 2
    assert out["latestAttemptAt"] == "2026-06-02T00:00:00Z"
    # firstPassedAt set on the first PASSING attempt.
    assert out["firstPassedAt"] == "2026-06-02T00:00:00Z"


async def test_lesson_rollup_non_improving_score_keeps_best(repo: DynamoProgressRepository) -> None:
    await repo.update_lesson_rollup(
        _USER, "l3", _attempt(cid="c1", lesson="l3", ts="2026-06-01T00:00:00Z", score=0.95, passed=True)
    )
    out = await repo.update_lesson_rollup(
        _USER, "l3", _attempt(cid="c2", lesson="l3", ts="2026-06-03T00:00:00Z", score=0.40, passed=True)
    )
    # Non-improving score → bestScore unchanged, but count + latest still bump.
    assert out["bestScore"] == 0.95
    assert out["attemptCount"] == 2
    assert out["latestAttemptAt"] == "2026-06-03T00:00:00Z"
    # firstPassedAt stays at the first passing attempt (if_not_exists).
    assert out["firstPassedAt"] == "2026-06-01T00:00:00Z"


async def test_lesson_rollup_first_pass_not_overwritten(repo: DynamoProgressRepository) -> None:
    # First attempt fails (no firstPassedAt), second improves + passes.
    await repo.update_lesson_rollup(
        _USER, "l4", _attempt(cid="c1", lesson="l4", ts="2026-06-01T00:00:00Z", score=0.2, passed=False)
    )
    out = await repo.update_lesson_rollup(
        _USER, "l4", _attempt(cid="c2", lesson="l4", ts="2026-06-02T00:00:00Z", score=0.7, passed=True)
    )
    assert out["firstPassedAt"] == "2026-06-02T00:00:00Z"
    # A later, lower passing attempt must not move firstPassedAt.
    out2 = await repo.update_lesson_rollup(
        _USER, "l4", _attempt(cid="c3", lesson="l4", ts="2026-06-05T00:00:00Z", score=0.6, passed=True)
    )
    assert out2["firstPassedAt"] == "2026-06-02T00:00:00Z"


# ── Day rollup ────────────────────────────────────────────────────────────────


async def test_day_rollup_increments(repo: DynamoProgressRepository) -> None:
    out = await repo.update_day_rollup(_USER, "2026-06-01", lessons_inc=1, minutes_inc=2, xp_inc=20)
    assert out == {"date": "2026-06-01", "lessonsCompleted": 1, "minutesActive": 2, "xpEarned": 20}
    out2 = await repo.update_day_rollup(_USER, "2026-06-01", lessons_inc=1, minutes_inc=3, xp_inc=15)
    assert out2["lessonsCompleted"] == 2
    assert out2["minutesActive"] == 5
    assert out2["xpEarned"] == 35
    # Read-back through the range query agrees.
    rows = await repo.get_day_rollups(_USER, "2026-06-01", "2026-06-01")
    assert rows[0]["xpEarned"] == 35


# ── Attempt idempotency ───────────────────────────────────────────────────────


async def test_put_attempt_idempotent_on_duplicate_client_id(repo: DynamoProgressRepository) -> None:
    a = _attempt(cid="dup-1", lesson="l9", ts="2026-06-01T00:00:00Z", score=0.8, passed=True)
    await repo.put_attempt(_USER, a)
    # Re-putting the same clientAttemptId is a no-op (conditional guard), not an error.
    await repo.put_attempt(_USER, a)
    found = await repo.attempt_exists(_USER, "dup-1")
    assert found is not None
    assert found["attemptId"] == "a-dup-1"
    # Only one attempt row exists for the lesson.
    items, _ = await repo.list_attempts(_USER, lesson_id="l9", limit=10)
    assert len(items) == 1


def _transaction_conflict() -> ClientError:
    """The exact prod cancellation shape: TransactWriteItems cancelled because a
    concurrent transaction holds the same items."""
    return ClientError(
        {
            "Error": {"Code": "TransactionCanceledException", "Message": "Transaction cancelled"},
            "CancellationReasons": [
                {"Code": "TransactionConflict", "Message": "Transaction is ongoing for the item"},
                {"Code": "TransactionConflict", "Message": "Transaction is ongoing for the item"},
            ],
        },
        "TransactWriteItems",
    )


async def test_put_attempt_retries_on_transaction_conflict(
    repo: DynamoProgressRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A transient TransactionConflict (concurrent double-submit) must be retried,
    # not surfaced as a 500. First call raises the conflict, second delegates to
    # the real client and lands the write.
    monkeypatch.setattr(progress_module, "_TRANSACT_BASE_BACKOFF_SEC", 0.0)
    client = repo._table.meta.client
    real = client.transact_write_items
    calls = {"n": 0}

    async def flaky(**kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise _transaction_conflict()
        return await real(**kwargs)

    monkeypatch.setattr(client, "transact_write_items", flaky)
    a = _attempt(cid="conflict-1", lesson="l10", ts="2026-07-18T01:37:00.756Z", score=0.75, passed=True)
    await repo.put_attempt(_USER, a)  # must not raise

    assert calls["n"] == 2  # one conflict, one success
    found = await repo.attempt_exists(_USER, "conflict-1")
    assert found is not None
    assert found["attemptId"] == "a-conflict-1"


async def test_put_attempt_reraises_after_exhausting_conflict_retries(
    repo: DynamoProgressRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A persistent conflict (never clears) must eventually re-raise rather than
    # loop forever — the retry budget is bounded.
    monkeypatch.setattr(progress_module, "_TRANSACT_BASE_BACKOFF_SEC", 0.0)
    client = repo._table.meta.client
    calls = {"n": 0}

    async def always_conflict(**kwargs: Any) -> Any:
        calls["n"] += 1
        raise _transaction_conflict()

    monkeypatch.setattr(client, "transact_write_items", always_conflict)
    a = _attempt(cid="conflict-2", lesson="l11", ts="2026-07-18T02:00:00.000Z", score=0.9, passed=True)
    with pytest.raises(ClientError):
        await repo.put_attempt(_USER, a)

    # Initial try + _TRANSACT_MAX_RETRIES retries.
    assert calls["n"] == progress_module._TRANSACT_MAX_RETRIES + 1
