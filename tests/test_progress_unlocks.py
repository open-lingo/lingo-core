"""Server-backed unlock map tests.

The client unlock ladder (`lingo:unlocked-atoms`) was localStorage-only, so a
storage clear / device switch lost progression. These tests pin the contract:

  - GET  /progress/me/unlocks   → full stored set
  - POST /progress/me/unlocks   → UNION newly-unlocked ids (never drops)

Route-level coverage runs against SQLite via the `api_client` fixture; a
separate round-trip exercises the DynamoDB user repo via moto so the
`settings.learning.unlockedAtoms` blob persists identically on both backends.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import boto3
import pytest
import pytest_asyncio
from moto.server import ThreadedMotoServer

_BASE = "/api/core/v1/progress/me/unlocks"


# ── Route-level (SQLite) ─────────────────────────────────────────────────────


def test_unlocks_empty_by_default(api_client) -> None:
    client, _user_id, _ = api_client
    resp = client.get(_BASE)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"unlockedAtoms": []}


def test_unlocks_add_and_read_back(api_client) -> None:
    client, _user_id, _ = api_client
    resp = client.post(_BASE, json={"atomIds": ["ja:a", "ja:b"]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["unlockedAtoms"] == ["ja:a", "ja:b"]

    # Survives across requests (the acceptance: storage clear / device switch
    # is simulated by a fresh read).
    resp = client.get(_BASE)
    assert resp.json()["unlockedAtoms"] == ["ja:a", "ja:b"]


def test_unlocks_union_never_drops(api_client) -> None:
    """A second push of a DISJOINT-but-overlapping subset must union, not
    replace. This is the core invariant — a stale device can't regress
    another device's unlocks."""
    client, _user_id, _ = api_client
    client.post(_BASE, json={"atomIds": ["ja:a", "ja:b"]})
    resp = client.post(_BASE, json={"atomIds": ["ja:b", "ja:c"]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["unlockedAtoms"] == ["ja:a", "ja:b", "ja:c"]


def test_unlocks_re_push_is_idempotent(api_client) -> None:
    client, _user_id, _ = api_client
    client.post(_BASE, json={"atomIds": ["ja:a"]})
    resp = client.post(_BASE, json={"atomIds": ["ja:a"]})
    assert resp.json()["unlockedAtoms"] == ["ja:a"]


def test_unlocks_does_not_clobber_other_learning_settings(api_client) -> None:
    """Pushing unlocks must deep-merge under `learning`, leaving sibling keys
    (e.g. learningLanguageId) intact."""
    client, _user_id, _ = api_client
    client.patch(
        "/api/core/v1/users/me/settings",
        json={"learning": {"learningLanguageId": "ja", "onboardingCompleted": True}},
    )
    client.post(_BASE, json={"atomIds": ["ja:a"]})

    settings = client.get("/api/core/v1/users/me/settings").json()
    learning = settings["learning"]
    assert learning["learningLanguageId"] == "ja"
    assert learning["onboardingCompleted"] is True
    assert learning["unlockedAtoms"] == ["ja:a"]


def test_unlocks_empty_push_is_noop(api_client) -> None:
    client, _user_id, _ = api_client
    client.post(_BASE, json={"atomIds": ["ja:a"]})
    resp = client.post(_BASE, json={"atomIds": []})
    assert resp.json()["unlockedAtoms"] == ["ja:a"]


# ── Repo round-trip (DynamoDB via moto) ──────────────────────────────────────
#
# The route stores via users.update_settings / reads via users.get_settings.
# Pinning the Dynamo user repo directly proves the blob persists + reads back
# identically on the prod backend, mirroring the SQLite path above.

_REGION = "us-east-1"
_TABLE = "lingo_users"


@pytest.fixture(scope="module")
def moto_server() -> Iterator[str]:
    server = ThreadedMotoServer(port=0)
    server.start()
    host, port = server.get_host_and_port()
    yield f"http://{host}:{port}"
    server.stop()


def _create_users_table(endpoint: str) -> None:
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
        ],
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    client.get_waiter("table_exists").wait(TableName=_TABLE)


@pytest_asyncio.fixture()
async def dynamo_user_repo(moto_server: str, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator:
    monkeypatch.setenv("AWS_ENDPOINT_URL_DYNAMODB", moto_server)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", _REGION)

    _create_users_table(moto_server)

    from app.db.dynamo import _session as dynamo_session
    from app.db.dynamo.user import DynamoUserRepository

    repo = DynamoUserRepository(_TABLE, _REGION)
    await repo.connect()
    try:
        yield repo
    finally:
        await dynamo_session.close_shared_resource()


async def test_dynamo_unlock_map_round_trip(dynamo_user_repo) -> None:
    """settings.learning.unlockedAtoms persists + reads back on Dynamo, and a
    second union write extends (does not replace) the set."""
    user_id = "u-dynamo-1"

    await dynamo_user_repo.update_settings(user_id, {"learning": {"unlockedAtoms": ["ja:a", "ja:b"]}})
    settings = await dynamo_user_repo.get_settings(user_id)
    assert settings["learning"]["unlockedAtoms"] == ["ja:a", "ja:b"]

    # Union write (the route computes the full unioned list, then stores it —
    # deep-merge replaces the list with the new full list).
    merged = sorted({"ja:a", "ja:b", "ja:c"})
    await dynamo_user_repo.update_settings(user_id, {"learning": {"unlockedAtoms": merged}})
    settings = await dynamo_user_repo.get_settings(user_id)
    assert settings["learning"]["unlockedAtoms"] == ["ja:a", "ja:b", "ja:c"]


async def test_dynamo_settings_numbers_are_native_not_decimal(dynamo_user_repo) -> None:
    """DynamoDB deserializes numbers as Decimal; get_settings must demote them
    to native int/float so consumers behave identically to the SQLite backend.
    Regression guard for the shop-inventory ``isinstance(v, (int, float))``
    filter, which a Decimal silently fails."""
    from decimal import Decimal

    user_id = "u-dynamo-nums"
    await dynamo_user_repo.update_settings(
        user_id, {"shop": {"inventory": {"streak-freeze": 3}}, "learning": {"targetRetention": 0.95}}
    )
    settings = await dynamo_user_repo.get_settings(user_id)

    qty = settings["shop"]["inventory"]["streak-freeze"]
    assert qty == 3
    assert type(qty) is int and not isinstance(qty, Decimal)
    assert isinstance(qty, (int, float))  # the exact filter used in purchase_shop_item

    retention = settings["learning"]["targetRetention"]
    assert type(retention) is float and not isinstance(retention, Decimal)


async def test_dynamo_shop_inventory_increments_across_purchases(dynamo_user_repo) -> None:
    """Mirror purchase_shop_item's inventory reconstruction against the prod
    backend: buying a consumable twice must stockpile to 2, and must not wipe a
    sibling consumable. Before the Decimal demotion fix the isinstance filter
    dropped the previously-stored Decimal counts, resetting quantity to 1 and
    losing other items on every purchase."""
    user_id = "u-dynamo-shop"

    # Seed an existing consumable stash (as a prior purchase would have).
    await dynamo_user_repo.update_settings(
        user_id, {"shop": {"inventory": {"streak-freeze": 1, "xp-boost": 2}}}
    )

    # Replay the router's reconstruction on the re-read settings.
    settings = await dynamo_user_repo.get_settings(user_id) or {}
    shop_state = dict(settings.get("shop") or {})
    inventory = {str(k): int(v) for k, v in (shop_state.get("inventory") or {}).items() if isinstance(v, (int, float))}
    inventory["streak-freeze"] = inventory.get("streak-freeze", 0) + 1
    shop_state["inventory"] = inventory
    await dynamo_user_repo.update_settings(user_id, {"shop": shop_state})

    final = await dynamo_user_repo.get_settings(user_id)
    assert final["shop"]["inventory"]["streak-freeze"] == 2
    assert final["shop"]["inventory"]["xp-boost"] == 2  # sibling not wiped
