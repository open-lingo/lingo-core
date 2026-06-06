"""DynamoDB-backed quest repository tests.

Mirrors the SQLite quest repo's semantics — same fixtures could be run
against ``SqliteQuestRepository`` and produce the same assertions. The
table schema here matches what Terraform provisions for ``lingo_quests``:
PK (S) + SK (S), PAY_PER_REQUEST, no GSIs.

Uses a module-scoped ``ThreadedMotoServer`` because moto's in-process
mock returns sync bytes that aiobotocore can't await (known
incompatibility). The threaded server serves real HTTP, and we point
aioboto3 at it via ``AWS_ENDPOINT_URL_DYNAMODB``. Pattern matches
``tests/test_platform_settings_dynamo.py`` + ``test_audit_dynamo.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import boto3
import pytest
import pytest_asyncio
from moto.server import ThreadedMotoServer

from app.db.dynamo import _session as dynamo_session
from app.db.dynamo.quests import DynamoQuestRepository

_REGION = "us-east-1"
_TABLE = "lingo_quests_test"


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
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    client.get_waiter("table_exists").wait(TableName=_TABLE)


@pytest_asyncio.fixture()
async def repo(
    moto_server: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[DynamoQuestRepository]:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
    monkeypatch.setenv("AWS_DEFAULT_REGION", _REGION)
    monkeypatch.setenv("AWS_ENDPOINT_URL_DYNAMODB", moto_server)

    # Reset shared-resource singleton so this test owns its own client.
    monkeypatch.setattr(dynamo_session, "_session", None)
    monkeypatch.setattr(dynamo_session, "_resource_ctx", None)
    monkeypatch.setattr(dynamo_session, "_resource", None)

    _create_table(moto_server)
    repo = DynamoQuestRepository(_TABLE, _REGION)
    await repo.connect()
    try:
        yield repo
    finally:
        await dynamo_session.close_shared_resource()


def _quest(quest_id: str, user_id: str = "u1", **overrides):
    base = {
        "id": quest_id,
        "user_id": user_id,
        "type": "daily",
        "title_key": "quests.daily.fiftyXp.title",
        "description_key": "quests.daily.fiftyXp.desc",
        "emoji": "⚡",
        "progress_current": 0,
        "progress_target": 50,
        "progress_unit": "XP",
        "reward_lingots": 5,
        "reward_xp": 10,
        "reward_ad_free_minutes": 0,
        "reward_streak_shield": False,
        "status": "active",
        "friend_id": None,
        "friend_display_name": None,
        "expires_at": "2099-12-31T00:00:00+00:00",
        "reward_granted": False,
        "created_at": "2026-06-01T00:00:00+00:00",
    }
    base.update(overrides)
    return base


# ─── Reads / writes round-trip ───────────────────────────────────────────────


async def test_put_and_get(repo: DynamoQuestRepository) -> None:
    persisted = await repo.put_quest(_quest("q1"))
    assert persisted["id"] == "q1"
    assert persisted["progress_target"] == 50
    assert persisted["reward_lingots"] == 5

    got = await repo.get_quest("u1", "q1")
    assert got is not None
    assert got["title_key"] == "quests.daily.fiftyXp.title"
    assert got["status"] == "active"
    assert got["reward_granted"] is False
    # Decimal -> int round-trip.
    assert isinstance(got["progress_current"], int)
    assert isinstance(got["progress_target"], int)


async def test_get_missing_returns_none(repo: DynamoQuestRepository) -> None:
    assert await repo.get_quest("u1", "ghost") is None


async def test_list_quests_newest_first(repo: DynamoQuestRepository) -> None:
    await repo.put_quest(_quest("q-older", created_at="2026-06-01T00:00:00+00:00"))
    await repo.put_quest(_quest("q-newer", created_at="2026-06-03T00:00:00+00:00"))
    await repo.put_quest(_quest("q-mid", created_at="2026-06-02T00:00:00+00:00"))

    rows = await repo.list_quests("u1")
    assert [r["id"] for r in rows] == ["q-newer", "q-mid", "q-older"]


async def test_list_quests_user_scoped(repo: DynamoQuestRepository) -> None:
    await repo.put_quest(_quest("mine", user_id="u1"))
    await repo.put_quest(_quest("theirs", user_id="u2"))

    mine = await repo.list_quests("u1")
    theirs = await repo.list_quests("u2")
    assert {r["id"] for r in mine} == {"mine"}
    assert {r["id"] for r in theirs} == {"theirs"}


# ─── update_progress semantics (mirror SQLite) ───────────────────────────────


async def test_update_progress_increments(repo: DynamoQuestRepository) -> None:
    await repo.put_quest(_quest("q", progress_target=50))
    updated = await repo.update_progress("u1", "q", 10)
    assert updated is not None
    assert updated["progress_current"] == 10
    assert updated["status"] == "active"


async def test_update_progress_caps_at_target_and_flips_claimable(
    repo: DynamoQuestRepository,
) -> None:
    await repo.put_quest(_quest("q", progress_target=50, progress_current=40))
    updated = await repo.update_progress("u1", "q", 25)
    assert updated is not None
    # Capped at target.
    assert updated["progress_current"] == 50
    # Status flipped active -> claimable.
    assert updated["status"] == "claimable"


async def test_update_progress_clamps_negative_at_zero(
    repo: DynamoQuestRepository,
) -> None:
    await repo.put_quest(_quest("q", progress_target=50, progress_current=5))
    updated = await repo.update_progress("u1", "q", -20)
    assert updated is not None
    assert updated["progress_current"] == 0


async def test_update_progress_no_op_when_completed(
    repo: DynamoQuestRepository,
) -> None:
    await repo.put_quest(
        _quest(
            "q",
            progress_target=50,
            progress_current=50,
            status="completed",
            reward_granted=True,
        ),
    )
    updated = await repo.update_progress("u1", "q", 5)
    assert updated is not None
    assert updated["status"] == "completed"
    assert updated["progress_current"] == 50


async def test_update_progress_missing_returns_none(repo: DynamoQuestRepository) -> None:
    assert await repo.update_progress("u1", "ghost", 5) is None


# ─── claim semantics ─────────────────────────────────────────────────────────


async def test_claim_when_claimable(repo: DynamoQuestRepository) -> None:
    await repo.put_quest(
        _quest("q", progress_target=50, progress_current=50, status="claimable"),
    )
    claimed = await repo.claim("u1", "q")
    assert claimed is not None
    assert claimed["status"] == "completed"
    assert claimed["reward_granted"] is True


async def test_claim_when_already_completed_is_idempotent(
    repo: DynamoQuestRepository,
) -> None:
    await repo.put_quest(
        _quest(
            "q",
            progress_target=50,
            progress_current=50,
            status="completed",
            reward_granted=True,
        ),
    )
    result = await repo.claim("u1", "q")
    assert result is not None
    assert result["status"] == "completed"
    assert result["reward_granted"] is True


async def test_claim_when_not_yet_claimable_returns_none(
    repo: DynamoQuestRepository,
) -> None:
    await repo.put_quest(_quest("q", progress_current=10, status="active"))
    assert await repo.claim("u1", "q") is None


async def test_claim_missing_returns_none(repo: DynamoQuestRepository) -> None:
    assert await repo.claim("u1", "ghost") is None


# ─── delete_user_quests ──────────────────────────────────────────────────────


async def test_delete_user_quests_all(repo: DynamoQuestRepository) -> None:
    await repo.put_quest(_quest("q1", type="daily"))
    await repo.put_quest(_quest("q2", type="weekly"))
    await repo.put_quest(_quest("q3", type="random"))

    n = await repo.delete_user_quests("u1")
    assert n == 3
    assert await repo.list_quests("u1") == []


async def test_delete_user_quests_by_type(repo: DynamoQuestRepository) -> None:
    await repo.put_quest(_quest("q1", type="daily"))
    await repo.put_quest(_quest("q2", type="daily"))
    await repo.put_quest(_quest("q3", type="weekly"))

    n = await repo.delete_user_quests("u1", types=["daily"])
    assert n == 2
    remaining = await repo.list_quests("u1")
    assert [r["id"] for r in remaining] == ["q3"]


async def test_delete_user_quests_other_users_untouched(
    repo: DynamoQuestRepository,
) -> None:
    await repo.put_quest(_quest("mine", user_id="u1"))
    await repo.put_quest(_quest("theirs", user_id="u2"))

    n = await repo.delete_user_quests("u1")
    assert n == 1
    assert {r["id"] for r in await repo.list_quests("u2")} == {"theirs"}


async def test_delete_user_quests_empty_returns_zero(repo: DynamoQuestRepository) -> None:
    assert await repo.delete_user_quests("u-nobody") == 0


# ─── Lifecycle smoke ─────────────────────────────────────────────────────────


async def test_update_progress_serial_calls_accumulate(
    repo: DynamoQuestRepository,
) -> None:
    await repo.put_quest(_quest("q", progress_target=10, progress_current=0))
    for _ in range(5):
        await repo.update_progress("u1", "q", 1)
    final = await repo.get_quest("u1", "q")
    assert final is not None
    assert final["progress_current"] == 5
    assert final["status"] == "active"


async def test_update_then_claim_full_lifecycle(repo: DynamoQuestRepository) -> None:
    await repo.put_quest(_quest("q", progress_target=3, progress_current=0))
    a = await repo.update_progress("u1", "q", 1)
    assert a and a["status"] == "active"
    b = await repo.update_progress("u1", "q", 1)
    assert b and b["status"] == "active"
    c = await repo.update_progress("u1", "q", 1)
    assert c and c["status"] == "claimable"

    claimed = await repo.claim("u1", "q")
    assert claimed and claimed["status"] == "completed" and claimed["reward_granted"]

    # Idempotent re-claim.
    again = await repo.claim("u1", "q")
    assert again and again["status"] == "completed"
