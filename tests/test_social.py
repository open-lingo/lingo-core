"""Social API happy-path tests.

Boots the full FastAPI app with SQLite on a temp DB, seeds two users (alice and
bob) plus a baseline activity item, and verifies the new endpoints. Auth is
short-circuited via ``DEBUG=true`` + the ``X-Dev-User`` header which is honored
by ``app.auth.dependencies.get_current_user``.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

# ── Helpers ──────────────────────────────────────────────────────────────────


def _register_user(
    client: TestClient, sub: str, username: str, display_name: str
) -> dict[str, Any]:
    """Create a user via POST /users/me, impersonating ``sub``."""
    resp = client.post(
        "/api/core/v1/users/me",
        json={"username": username, "display_name": display_name},
        headers={"X-Dev-User": sub},
    )
    assert resp.status_code in (200, 201, 409), resp.text
    if resp.status_code == 409:
        # Already exists — fetch.
        resp = client.get("/api/core/v1/users/me", headers={"X-Dev-User": sub})
        assert resp.status_code == 200, resp.text
    return resp.json()


def _as(sub: str) -> dict[str, str]:
    return {"X-Dev-User": sub}


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def client() -> Any:
    tmp_db = os.path.join(tempfile.mkdtemp(prefix="lingo-social-"), "social.db")
    os.environ["DB_BACKEND"] = "sqlite"
    os.environ["SQLITE_PATH"] = tmp_db
    os.environ["DEBUG"] = "true"
    os.environ["DEV_USER"] = "auth0|alice"

    import importlib

    from app import config as config_mod
    importlib.reload(config_mod)
    from app.db import provider as provider_mod
    importlib.reload(provider_mod)
    from app.auth import dependencies as auth_dep_mod
    importlib.reload(auth_dep_mod)
    from app import main as main_mod
    importlib.reload(main_mod)

    with TestClient(main_mod.app) as c:
        yield c


@pytest.fixture(scope="module")
def users(client: TestClient) -> dict[str, dict[str, Any]]:
    alice = _register_user(client, "auth0|alice", "alice_t", "Alice T")
    bob = _register_user(client, "auth0|bob", "bob_t", "Bob T")
    carol = _register_user(client, "auth0|carol", "carol_t", "Carol T")
    return {"alice": alice, "bob": bob, "carol": carol}


# ── Friends ──────────────────────────────────────────────────────────────────


def test_send_and_accept_friend_request(
    client: TestClient, users: dict[str, dict[str, Any]]
) -> None:
    bob_id = users["bob"]["id"]
    # Alice sends a request to Bob.
    resp = client.post(
        "/api/core/v1/social/friends/requests",
        json={"to_user_id": bob_id},
        headers=_as("auth0|alice"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "pending"

    # Bob sees an incoming request.
    resp = client.get("/api/core/v1/social/friends/requests", headers=_as("auth0|bob"))
    assert resp.status_code == 200
    body = resp.json()
    assert any(r["username"] == "alice_t" for r in body["incoming"])

    # Bob accepts.
    alice_id = users["alice"]["id"]
    resp = client.post(
        f"/api/core/v1/social/friends/requests/{alice_id}/accept",
        headers=_as("auth0|bob"),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"

    # Both list each other as friends.
    resp = client.get("/api/core/v1/social/friends", headers=_as("auth0|alice"))
    assert any(f["user_id"] == bob_id for f in resp.json())
    resp = client.get("/api/core/v1/social/friends", headers=_as("auth0|bob"))
    assert any(f["user_id"] == alice_id for f in resp.json())


def test_block_and_unblock(client: TestClient, users: dict[str, dict[str, Any]]) -> None:
    carol_id = users["carol"]["id"]
    resp = client.post(
        f"/api/core/v1/social/blocks/{carol_id}", headers=_as("auth0|alice")
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "blocked"}

    resp = client.get("/api/core/v1/social/blocks", headers=_as("auth0|alice"))
    assert resp.status_code == 200
    assert any(b["user_id"] == carol_id for b in resp.json())

    resp = client.delete(
        f"/api/core/v1/social/blocks/{carol_id}", headers=_as("auth0|alice")
    )
    assert resp.status_code == 204


# ── Public profile ───────────────────────────────────────────────────────────


def test_public_profile_friendship_status(
    client: TestClient, users: dict[str, dict[str, Any]]
) -> None:
    # Bob is already Alice's friend by the time this runs (test_send_and_accept).
    resp = client.get(
        "/api/core/v1/social/profiles/bob_t", headers=_as("auth0|alice")
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["username"] == "bob_t"
    assert body["friendship_status"] == "friend"


def test_public_profile_enriched_fields(
    client: TestClient, users: dict[str, dict[str, Any]]
) -> None:
    """Enriched fields are present + typed correctly even when zero-valued."""
    resp = client.get(
        "/api/core/v1/social/profiles/bob_t", headers=_as("auth0|alice")
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Enrichment fields appended in Task 2.
    assert "lingots" in body and isinstance(body["lingots"], int)
    assert "level" in body and isinstance(body["level"], int)
    assert "last_active_date" in body  # may be None
    assert "authored_deck_count" in body
    assert isinstance(body["authored_deck_count"], int)
    assert "authored_decks_sample" in body
    assert isinstance(body["authored_decks_sample"], list)
    # Sample respects the 5-deck cap defined in the handler.
    assert len(body["authored_decks_sample"]) <= 5


# ── Leaderboards ─────────────────────────────────────────────────────────────


def test_leaderboards_all_buckets(client: TestClient, users: dict[str, dict[str, Any]]) -> None:
    for path in (
        "/api/core/v1/social/leaderboards/weekly",
        "/api/core/v1/social/leaderboards/monthly",
        "/api/core/v1/social/leaderboards/friends",
    ):
        resp = client.get(path, headers=_as("auth0|alice"))
        assert resp.status_code == 200, f"{path}: {resp.text}"
        body = resp.json()
        assert "entries" in body
        assert "bucket" in body
        assert "total" in body

    resp = client.get("/api/core/v1/social/leaderboards/me", headers=_as("auth0|alice"))
    assert resp.status_code == 200
    body = resp.json()
    assert "weekly" in body and "monthly" in body


def test_league_spotlight(client: TestClient, users: dict[str, dict[str, Any]]) -> None:
    resp = client.get(
        "/api/core/v1/social/leaderboards/spotlight", headers=_as("auth0|alice")
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["league"] in {"bronze", "silver", "gold", "diamond", "obsidian"}
    assert isinstance(body["league_tier"], int)
    assert isinstance(body["top_three"], list)
    assert isinstance(body["daily_xp"], int)
    # Bronze for a fresh user — promotion threshold should be set.
    if body["league"] == "bronze":
        assert body["promotion_threshold"] == 100


# ── Streak snapshot ──────────────────────────────────────────────────────────


def test_streak_snapshot(client: TestClient, users: dict[str, dict[str, Any]]) -> None:
    resp = client.get(
        "/api/core/v1/social/streak-snapshot", headers=_as("auth0|alice")
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "my_streak_days" in body
    assert "friend_median_streak_days" in body


# ── Activity feed + reactions ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_activity_feed_and_reaction_toggle(
    client: TestClient, users: dict[str, dict[str, Any]]
) -> None:
    """Seed a single activity row via the repo, then exercise GET + reactions."""
    from app.db.provider import get_social_repo

    repo = get_social_repo()
    activity_id = str(uuid.uuid4())
    await repo.put_activity(
        {
            "id": activity_id,
            "user_id": users["alice"]["id"],
            "kind": "lesson_completed",
            "payload": {"lessonId": "ja-m1-l1", "xp": 25},
            "created_at": datetime.now(UTC).isoformat(),
        }
    )

    # Feed contains it.
    resp = client.get("/api/core/v1/social/activity", headers=_as("auth0|alice"))
    assert resp.status_code == 200, resp.text
    feed = resp.json()
    activity_ids = [i["id"] for i in feed["items"]]
    assert activity_id in activity_ids
    item = next(i for i in feed["items"] if i["id"] == activity_id)
    assert any(r["kind"] == "wave" for r in item["reactions"])
    assert all(r["count"] == 0 for r in item["reactions"])
    assert all(r["mine"] is False for r in item["reactions"])

    # Bob reacts with "fire" — count goes to 1 (his), Alice still sees mine=False.
    resp = client.post(
        f"/api/core/v1/social/activity/{activity_id}/reactions/fire",
        headers=_as("auth0|bob"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"kind": "fire", "count": 1, "mine": True}

    # Re-fetch as Alice — reaction shows count=1, mine=False.
    resp = client.get("/api/core/v1/social/activity", headers=_as("auth0|alice"))
    assert resp.status_code == 200
    item = next(i for i in resp.json()["items"] if i["id"] == activity_id)
    fire = next(r for r in item["reactions"] if r["kind"] == "fire")
    assert fire["count"] == 1
    assert fire["mine"] is False

    # Alice also reacts "fire" — count 2.
    resp = client.post(
        f"/api/core/v1/social/activity/{activity_id}/reactions/fire",
        headers=_as("auth0|alice"),
    )
    assert resp.status_code == 200
    assert resp.json() == {"kind": "fire", "count": 2, "mine": True}

    # Alice toggles off — count drops to 1, mine=False.
    resp = client.post(
        f"/api/core/v1/social/activity/{activity_id}/reactions/fire",
        headers=_as("auth0|alice"),
    )
    assert resp.status_code == 200
    assert resp.json() == {"kind": "fire", "count": 1, "mine": False}


# ── Invites ──────────────────────────────────────────────────────────────────


def test_invite_offer_returns_persistent_code(
    client: TestClient, users: dict[str, dict[str, Any]]
) -> None:
    resp = client.get("/api/core/v1/social/invites/offer", headers=_as("auth0|alice"))
    assert resp.status_code == 200, resp.text
    first = resp.json()
    assert len(first["code"]) == 8
    assert first["url"].endswith(f"/{first['code']}")
    assert first["monthly_cap"] == 10
    assert first["first_lesson_required"] is True

    resp = client.get("/api/core/v1/social/invites/offer", headers=_as("auth0|alice"))
    assert resp.status_code == 200
    second = resp.json()
    assert second["code"] == first["code"]


def test_invite_redemption_paths(
    client: TestClient, users: dict[str, dict[str, Any]]
) -> None:
    # Alice's code, fetched fresh.
    resp = client.get("/api/core/v1/social/invites/offer", headers=_as("auth0|alice"))
    assert resp.status_code == 200
    code = resp.json()["code"]

    # Alice tries to redeem her own code → self.
    resp = client.post(
        f"/api/core/v1/social/invites/redeem/{code}", headers=_as("auth0|alice")
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "self"

    # Bob redeems → pending.
    resp = client.post(
        f"/api/core/v1/social/invites/redeem/{code}", headers=_as("auth0|bob")
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"

    # Same call again → still pending (idempotent).
    resp = client.post(
        f"/api/core/v1/social/invites/redeem/{code}", headers=_as("auth0|bob")
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"

    # Invalid code → invalid.
    resp = client.post(
        "/api/core/v1/social/invites/redeem/NOTACODE", headers=_as("auth0|carol")
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "invalid"


# ── Threads ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_threads_listing_and_detail(
    client: TestClient, users: dict[str, dict[str, Any]]
) -> None:
    from app.db.provider import get_social_repo

    repo = get_social_repo()
    thread_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    await repo.put_thread(
        {
            "id": thread_id,
            "user_a_id": users["alice"]["id"],
            "user_b_id": users["bob"]["id"],
            "created_at": now,
            "updated_at": now,
        }
    )
    await repo.put_message(
        {
            "id": str(uuid.uuid4()),
            "thread_id": thread_id,
            "sender_id": users["bob"]["id"],
            "body": "hey alice!",
            "sent_at": now,
        }
    )

    resp = client.get("/api/core/v1/social/threads", headers=_as("auth0|alice"))
    assert resp.status_code == 200, resp.text
    threads = resp.json()
    found = next((t for t in threads if t["id"] == thread_id), None)
    assert found is not None
    assert found["other_username"] == "bob_t"
    assert found["last_message_preview"] == "hey alice!"

    resp = client.get(
        f"/api/core/v1/social/threads/{thread_id}", headers=_as("auth0|alice")
    )
    assert resp.status_code == 200, resp.text
    detail = resp.json()
    assert len(detail["messages"]) == 1
    assert detail["messages"][0]["body"] == "hey alice!"

    # Carol is not a participant.
    resp = client.get(
        f"/api/core/v1/social/threads/{thread_id}", headers=_as("auth0|carol")
    )
    assert resp.status_code == 403


# ── Friend quest helpers ─────────────────────────────────────────────────────


def test_quest_targets(client: TestClient, users: dict[str, dict[str, Any]]) -> None:
    resp = client.get("/api/core/v1/social/quest-targets", headers=_as("auth0|alice"))
    assert resp.status_code == 200, resp.text
    targets = resp.json()
    # Bob is a friend with 0 streak and 0 weekly XP — should be reachable for
    # both axes.
    bob_target = next((t for t in targets if t["username"] == "bob_t"), None)
    assert bob_target is not None
    assert "streak" in bob_target["reachable_for"]
    assert "weekly_xp" in bob_target["reachable_for"]
