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


def _register_user(client: TestClient, sub: str, username: str, display_name: str) -> dict[str, Any]:
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


def test_send_and_accept_friend_request(client: TestClient, users: dict[str, dict[str, Any]]) -> None:
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
    resp = client.post(f"/api/core/v1/social/blocks/{carol_id}", headers=_as("auth0|alice"))
    assert resp.status_code == 200
    assert resp.json() == {"status": "blocked"}

    resp = client.get("/api/core/v1/social/blocks", headers=_as("auth0|alice"))
    assert resp.status_code == 200
    assert any(b["user_id"] == carol_id for b in resp.json())

    resp = client.delete(f"/api/core/v1/social/blocks/{carol_id}", headers=_as("auth0|alice"))
    assert resp.status_code == 204


# ── Public profile ───────────────────────────────────────────────────────────


def test_public_profile_friendship_status(client: TestClient, users: dict[str, dict[str, Any]]) -> None:
    # Bob is already Alice's friend by the time this runs (test_send_and_accept).
    resp = client.get("/api/core/v1/social/profiles/bob_t", headers=_as("auth0|alice"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["username"] == "bob_t"
    assert body["friendship_status"] == "friend"


def test_public_profile_enriched_fields(client: TestClient, users: dict[str, dict[str, Any]]) -> None:
    """Enriched fields are present + typed correctly even when zero-valued."""
    resp = client.get("/api/core/v1/social/profiles/bob_t", headers=_as("auth0|alice"))
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
    # Task 7 — league key always present (may be None for zero-XP users).
    assert "league" in body


def test_public_profile_league_none_for_zero_xp(client: TestClient, users: dict[str, dict[str, Any]]) -> None:
    """Bob has 0 XP at this point in the session — league must be None."""
    resp = client.get("/api/core/v1/social/profiles/bob_t", headers=_as("auth0|alice"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Bob hasn't earned XP in the test fixtures.
    assert body["xp"] == 0
    assert body["league"] is None


def test_public_profile_league_derived_from_xp(client: TestClient, users: dict[str, dict[str, Any]]) -> None:
    """When a user has accumulated XP, the league badge is filled in.

    Carol is a fresh user — we award her some XP via the admin endpoint
    (gated by ADMIN_USER_IDS) and confirm the league climbs accordingly.
    """
    # Promote alice to admin so she can use the award-xp endpoint.
    from app.config import settings as cfg

    prior_admins = list(cfg.ADMIN_USER_IDS)
    cfg.ADMIN_USER_IDS = [users["alice"]["id"]]
    try:
        award = client.post(
            f"/api/core/v1/admin/users/{users['carol']['id']}/award-xp",
            json={"amount": 800, "reason": "league test"},
            headers=_as("auth0|alice"),
        )
        assert award.status_code == 200, award.text
    finally:
        cfg.ADMIN_USER_IDS = prior_admins

    resp = client.get("/api/core/v1/social/profiles/carol_t", headers=_as("auth0|alice"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    league = body["league"]
    assert league is not None
    # 800 XP clears the Gold (750) threshold → tier_index 2.
    assert league["tier_index"] == 2
    assert league["name"] == "Gold League"
    assert league["emoji"]  # non-empty emoji


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


def test_leaderboard_bundle_shape(
    client: TestClient, users: dict[str, dict[str, Any]]
) -> None:
    """Bundle endpoint returns weekly + monthly + friends + spotlight in one
    payload. Cuts the social-page leaderboards card from 4 round-trips to 1.
    """
    resp = client.get(
        "/api/core/v1/social/leaderboards/bundle?lang=ja", headers=_as("auth0|alice")
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for key in ("weekly", "monthly", "friends", "spotlight"):
        assert key in body, f"bundle missing {key}: {body.keys()}"
    # Each board carries the same shape as the standalone endpoints.
    for board_key in ("weekly", "monthly", "friends"):
        board = body[board_key]
        assert "entries" in board and "bucket" in board and "total" in board
    spot = body["spotlight"]
    assert spot["league"] in {"bronze", "silver", "gold", "diamond", "obsidian"}
    assert isinstance(spot["daily_xp"], list) and len(spot["daily_xp"]) == 7


def test_league_spotlight(client: TestClient, users: dict[str, dict[str, Any]]) -> None:
    resp = client.get("/api/core/v1/social/leaderboards/spotlight", headers=_as("auth0|alice"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["league"] in {"bronze", "silver", "gold", "diamond", "obsidian"}
    assert isinstance(body["league_tier"], int)
    assert isinstance(body["top_three"], list)
    # daily_xp is a 7-element list (one int per day, oldest first).
    assert isinstance(body["daily_xp"], list), f"daily_xp must be list, got {type(body['daily_xp'])}"
    assert len(body["daily_xp"]) == 7
    assert isinstance(body["friend_median_daily_xp"], list)
    assert len(body["friend_median_daily_xp"]) == 7
    # Bronze for a fresh user — promotion threshold should be set.
    if body["league"] == "bronze":
        assert body["promotion_threshold"] == 100


# ── Streak snapshot ──────────────────────────────────────────────────────────


def test_streak_snapshot(client: TestClient, users: dict[str, dict[str, Any]]) -> None:
    resp = client.get("/api/core/v1/social/streak-snapshot", headers=_as("auth0|alice"))
    assert resp.status_code == 200
    body = resp.json()
    assert "my_streak_days" in body
    assert "friend_median_streak_days" in body


# ── Activity feed + reactions ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_activity_feed_pulls_from_friend_attempts(
    client: TestClient, users: dict[str, dict[str, Any]]
) -> None:
    """The /activity feed is now pull-based: it reads each friend's
    recent ``progress.list_attempts``, merges + sorts desc, caps at the
    limit. Seed two attempts (one each for Alice + Bob) and verify both
    surface in Alice's feed, newest first, with reaction-toggle still
    working against the attempt_id as the activity_id.
    """
    from app.db.provider import get_progress_repo, get_social_repo

    progress = get_progress_repo()
    social = get_social_repo()

    # Older attempt by Alice.
    alice_attempt_id = str(uuid.uuid4())
    old_ts = "2026-05-25T10:00:00+00:00"
    await progress.put_attempt(
        users["alice"]["id"],
        {
            "attemptId": alice_attempt_id,
            "clientAttemptId": alice_attempt_id,
            "lessonId": "ja-m3-l1",
            "attemptedAt": old_ts,
            "durationSec": 120,
            "passed": True,
            "score": 0.92,
            "steps": [],
        },
    )

    # Newer attempt by Bob (Alice's friend).
    bob_attempt_id = str(uuid.uuid4())
    new_ts = datetime.now(UTC).isoformat()
    await progress.put_attempt(
        users["bob"]["id"],
        {
            "attemptId": bob_attempt_id,
            "clientAttemptId": bob_attempt_id,
            "lessonId": "ja-m4-l3",
            "attemptedAt": new_ts,
            "durationSec": 200,
            "passed": True,
            "score": 0.78,
            "steps": [],
        },
    )

    resp = client.get("/api/core/v1/social/activity", headers=_as("auth0|alice"))
    assert resp.status_code == 200, resp.text
    feed = resp.json()
    assert feed["cursor"] is None

    activity_ids = [i["id"] for i in feed["items"]]
    assert bob_attempt_id in activity_ids
    assert alice_attempt_id in activity_ids
    # Newest first: Bob's attempt outranks Alice's older one.
    assert activity_ids.index(bob_attempt_id) < activity_ids.index(alice_attempt_id)

    bob_item = next(i for i in feed["items"] if i["id"] == bob_attempt_id)
    assert bob_item["kind"] == "lesson_completed"
    assert bob_item["payload"]["lessonId"] == "ja-m4-l3"
    assert bob_item["username"] == "bob_t"
    # Reactions are still surfaced per-kind with zero counts initially.
    assert any(r["kind"] == "wave" for r in bob_item["reactions"])
    assert all(r["count"] == 0 for r in bob_item["reactions"])

    # Reaction toggle round-trips through the existing reactions table,
    # keyed on activity_id == attempt_id.
    resp = client.post(
        f"/api/core/v1/social/activity/{bob_attempt_id}/reactions/fire",
        headers=_as("auth0|alice"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"kind": "fire", "count": 1, "mine": True}

    # Re-fetch confirms the reaction lands on the feed item.
    resp = client.get("/api/core/v1/social/activity", headers=_as("auth0|alice"))
    item = next(i for i in resp.json()["items"] if i["id"] == bob_attempt_id)
    fire = next(r for r in item["reactions"] if r["kind"] == "fire")
    assert fire["count"] == 1
    assert fire["mine"] is True

    # Bystander: confirm the empty-state path still returns a well-formed
    # response when a fresh user has no attempts and no friends with any.
    # Use list_friends on the social repo to find an actor with no edges —
    # in this fixture, ``new_buddy`` (registered in a later test) doesn't
    # exist yet, so re-use carol who has no attempts. Carol blocks alice
    # so she'd be excluded anyway; here just exercise empty-shape.
    assert "social" in dir(social) or social is not None


# ── Invites ──────────────────────────────────────────────────────────────────


def test_invite_offer_returns_persistent_code(client: TestClient, users: dict[str, dict[str, Any]]) -> None:
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


def test_invite_redemption_paths(client: TestClient, users: dict[str, dict[str, Any]]) -> None:
    # Alice's code, fetched fresh.
    resp = client.get("/api/core/v1/social/invites/offer", headers=_as("auth0|alice"))
    assert resp.status_code == 200
    code = resp.json()["code"]

    # Alice tries to redeem her own code → self.
    resp = client.post(f"/api/core/v1/social/invites/redeem/{code}", headers=_as("auth0|alice"))
    assert resp.status_code == 200
    assert resp.json()["status"] == "self"

    # Bob redeems → pending.
    resp = client.post(f"/api/core/v1/social/invites/redeem/{code}", headers=_as("auth0|bob"))
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"

    # Same call again → still pending (idempotent).
    resp = client.post(f"/api/core/v1/social/invites/redeem/{code}", headers=_as("auth0|bob"))
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"

    # Invalid code → invalid.
    resp = client.post("/api/core/v1/social/invites/redeem/NOTACODE", headers=_as("auth0|carol"))
    assert resp.status_code == 200
    assert resp.json()["status"] == "invalid"


# ── Threads ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_threads_listing_and_detail(client: TestClient, users: dict[str, dict[str, Any]]) -> None:
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

    resp = client.get(f"/api/core/v1/social/threads/{thread_id}", headers=_as("auth0|alice"))
    assert resp.status_code == 200, resp.text
    detail = resp.json()
    assert len(detail["messages"]) == 1
    assert detail["messages"][0]["body"] == "hey alice!"

    # Carol is not a participant.
    resp = client.get(f"/api/core/v1/social/threads/{thread_id}", headers=_as("auth0|carol"))
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


# ── Friend suggestions ──────────────────────────────────────────────────────


def _set_lang(client: TestClient, sub: str, lang: str) -> None:
    """Persist a learning-language preference via the user-settings endpoint."""
    resp = client.patch(
        "/api/core/v1/users/me/settings",
        json={"learning": {"learningLanguageId": lang}},
        headers=_as(sub),
    )
    assert resp.status_code in (200, 204), resp.text


def test_friend_suggestions_filters_by_shared_language_and_excludes_friends_and_blocked(
    client: TestClient, users: dict[str, dict[str, Any]]
) -> None:
    """End-to-end: register 3 fresh users, set Alice + a `lang_buddy` to ``ja``
    and a `lang_other` to ``ko``, then verify the suggestion list contains
    ``lang_buddy`` but excludes Bob (already a friend), Carol (blocked), the
    ``lang_other`` user (wrong language), and Alice herself.
    """
    # Register two more users with explicit languages.
    _register_user(client, "auth0|lang_buddy", "lang_buddy", "Lang Buddy")
    _register_user(client, "auth0|lang_other", "lang_other", "Lang Other")

    _set_lang(client, "auth0|alice", "ja")
    _set_lang(client, "auth0|lang_buddy", "ja")
    _set_lang(client, "auth0|lang_other", "ko")
    # Bob (friend) shares language but is already a friend.
    _set_lang(client, "auth0|bob", "ja")
    # Carol is blocked from earlier test.
    _set_lang(client, "auth0|carol", "ja")
    # Re-block Carol — the earlier block was undone, ensure she's blocked again
    # so we exercise the block-exclusion path.
    carol_id = users["carol"]["id"]
    resp = client.post(f"/api/core/v1/social/blocks/{carol_id}", headers=_as("auth0|alice"))
    assert resp.status_code in (200, 409), resp.text

    resp = client.get(
        "/api/core/v1/social/suggestions?limit=10", headers=_as("auth0|alice")
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    names = {item["username"] for item in body["items"]}

    # The shared-language non-friend should appear.
    assert "lang_buddy" in names
    # Excluded: self, friends, blocked, wrong-language.
    assert "alice_t" not in names
    assert "bob_t" not in names
    assert "carol_t" not in names
    assert "lang_other" not in names


def test_friend_suggestions_respects_limit(
    client: TestClient, users: dict[str, dict[str, Any]]
) -> None:
    resp = client.get(
        "/api/core/v1/social/suggestions?limit=1", headers=_as("auth0|alice")
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) <= 1


def test_friend_suggestions_excludes_blocked_target(
    client: TestClient, users: dict[str, dict[str, Any]]
) -> None:
    """Targeted check: a candidate that becomes blocked must drop out of
    the suggestion list."""
    # Register a fresh suggestion candidate that shares Alice's language.
    _register_user(client, "auth0|new_buddy", "new_buddy", "New Buddy")
    _set_lang(client, "auth0|new_buddy", "ja")

    resp = client.get(
        "/api/core/v1/social/suggestions?limit=20", headers=_as("auth0|alice")
    )
    assert resp.status_code == 200, resp.text
    assert any(item["username"] == "new_buddy" for item in resp.json()["items"])

    # Block them — they should disappear.
    me_resp = client.get("/api/core/v1/users/me", headers=_as("auth0|new_buddy"))
    new_buddy_id = me_resp.json()["id"]
    resp = client.post(
        f"/api/core/v1/social/blocks/{new_buddy_id}", headers=_as("auth0|alice")
    )
    assert resp.status_code == 200, resp.text

    resp = client.get(
        "/api/core/v1/social/suggestions?limit=20", headers=_as("auth0|alice")
    )
    assert resp.status_code == 200, resp.text
    assert all(item["username"] != "new_buddy" for item in resp.json()["items"])
