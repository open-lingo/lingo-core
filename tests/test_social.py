"""Social API tests — friends, blocks, leaderboards, profile visibility.

These use the shared ``api_client`` fixture (DEBUG=true + X-Dev-User header
bypass) and the per-test SQLite path so the social tables are isolated.
"""

import uuid
from collections.abc import Iterator

import pytest

# ── Helpers ────────────────────────────────────────────────────────────────


def _register_user(client, dev_sub: str) -> tuple[str, str]:
    """Register a fresh user under the given dev sub. Returns (user_id, username)."""
    username = f"u{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/api/core/v1/users/me",
        json={"username": username, "display_name": f"User {username}"},
        headers={"X-Dev-User": dev_sub},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"], username


def _hdr(sub: str) -> dict[str, str]:
    return {"X-Dev-User": sub}


# ── Friend graph ────────────────────────────────────────────────────────────


def test_send_and_accept_friend_request(api_client) -> None:
    client, user_id, _admin_id = api_client

    # Register a second target user.
    other_sub = f"dev|other-{uuid.uuid4().hex[:6]}"
    other_id, other_username = _register_user(client, other_sub)

    # User A sends request to B by username.
    resp = client.post(
        "/api/core/v1/social/friends/requests",
        json={"toUsername": other_username},
        headers=_hdr("dev|test-user"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "pending"

    # B sees it in incoming.
    resp = client.get(
        "/api/core/v1/social/friends/requests",
        headers=_hdr(other_sub),
    )
    assert resp.status_code == 200
    bundle = resp.json()
    assert any(it["user_id"] == user_id for it in bundle["incoming"])
    assert bundle["outgoing"] == []

    # A sees it in outgoing.
    resp = client.get(
        "/api/core/v1/social/friends/requests",
        headers=_hdr("dev|test-user"),
    )
    assert resp.status_code == 200
    bundle = resp.json()
    assert any(it["user_id"] == other_id for it in bundle["outgoing"])

    # B accepts.
    resp = client.post(
        f"/api/core/v1/social/friends/requests/{user_id}/accept",
        headers=_hdr(other_sub),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"

    # Both see each other as friends.
    resp = client.get("/api/core/v1/social/friends", headers=_hdr("dev|test-user"))
    assert resp.status_code == 200
    friend_ids_a = [it["user_id"] for it in resp.json()]
    assert other_id in friend_ids_a

    resp = client.get("/api/core/v1/social/friends", headers=_hdr(other_sub))
    assert resp.status_code == 200
    friend_ids_b = [it["user_id"] for it in resp.json()]
    assert user_id in friend_ids_b


def test_cannot_request_self(api_client) -> None:
    client, _user_id, _admin_id = api_client
    # Get my username back so we can try to friend by username.
    resp = client.get("/api/core/v1/users/me", headers=_hdr("dev|test-user"))
    me = resp.json()
    resp = client.post(
        "/api/core/v1/social/friends/requests",
        json={"toUsername": me["username"]},
        headers=_hdr("dev|test-user"),
    )
    assert resp.status_code == 400


def test_cannot_request_blocked_user(api_client) -> None:
    """A blocks B → B's request to A is rejected with 404 (blocker invisible)."""
    client, user_id, _admin_id = api_client

    other_sub = f"dev|blocker-{uuid.uuid4().hex[:6]}"
    other_id, _other_username = _register_user(client, other_sub)

    # A (test-user) blocks B (other).
    resp = client.post(
        f"/api/core/v1/social/blocks/{other_id}",
        headers=_hdr("dev|test-user"),
    )
    assert resp.status_code == 200

    # B's request to A returns 404 (blocker is invisible).
    me = client.get("/api/core/v1/users/me", headers=_hdr("dev|test-user")).json()
    resp = client.post(
        "/api/core/v1/social/friends/requests",
        json={"toUsername": me["username"]},
        headers=_hdr(other_sub),
    )
    assert resp.status_code == 404
    _ = user_id  # silence unused


def test_block_cascades_friendship(api_client) -> None:
    client, user_id, _admin_id = api_client

    other_sub = f"dev|friend-{uuid.uuid4().hex[:6]}"
    other_id, other_username = _register_user(client, other_sub)

    # Become friends.
    client.post(
        "/api/core/v1/social/friends/requests",
        json={"toUsername": other_username},
        headers=_hdr("dev|test-user"),
    )
    client.post(
        f"/api/core/v1/social/friends/requests/{user_id}/accept",
        headers=_hdr(other_sub),
    )

    # Sanity: friendship visible from both sides.
    resp = client.get("/api/core/v1/social/friends", headers=_hdr("dev|test-user"))
    assert any(it["user_id"] == other_id for it in resp.json())

    # A blocks B.
    resp = client.post(
        f"/api/core/v1/social/blocks/{other_id}",
        headers=_hdr("dev|test-user"),
    )
    assert resp.status_code == 200

    # Both FRIEND rows must be gone.
    resp = client.get("/api/core/v1/social/friends", headers=_hdr("dev|test-user"))
    assert all(it["user_id"] != other_id for it in resp.json())
    resp = client.get("/api/core/v1/social/friends", headers=_hdr(other_sub))
    assert all(it["user_id"] != user_id for it in resp.json())

    # Block row exists.
    resp = client.get("/api/core/v1/social/blocks", headers=_hdr("dev|test-user"))
    assert any(it["user_id"] == other_id for it in resp.json())


def test_community_banned_user_blocked_from_social(api_client) -> None:
    """A community-banned user gets 403 on every social endpoint."""
    client, _user_id, _admin_id = api_client

    # Ban "dev|test-user" via the user repo directly (bypassing admin endpoints,
    # which aren't role-gated yet but live outside this test's scope).
    from app.db.provider import get_user_repo

    repo = get_user_repo()

    import asyncio

    async def _ban() -> None:
        record = await repo.get_user_by_auth0_id("dev|test-user")
        await repo.update_user(record["id"], {"community_status": "banned"})

    asyncio.get_event_loop().run_until_complete(_ban())

    for method, path in (
        ("get", "/api/core/v1/social/friends"),
        ("get", "/api/core/v1/social/friends/requests"),
        ("get", "/api/core/v1/social/blocks"),
        ("get", "/api/core/v1/social/leaderboards/ja/weekly"),
        ("get", "/api/core/v1/social/leaderboards/ja/monthly"),
        ("get", "/api/core/v1/social/leaderboards/friends"),
        ("get", "/api/core/v1/social/leaderboards/me"),
    ):
        resp = getattr(client, method)(path, headers=_hdr("dev|test-user"))
        assert resp.status_code == 403, f"{method} {path} → {resp.status_code}"
        assert resp.json()["detail"]["code"] == "COMMUNITY_BANNED"


# ── Leaderboard opt-in ─────────────────────────────────────────────────────


def _submit_lesson(client, sub: str, lesson_id: str, client_attempt_id: str):
    return client.post(
        "/api/core/v1/progress/lessons/batch",
        json={
            "checkStreak": True,
            "attempts": [
                {
                    "clientAttemptId": client_attempt_id,
                    "lessonId": lesson_id,
                    "attemptedAt": "2026-05-25T12:00:00+00:00",
                    "durationSec": 60,
                    "passed": True,
                    "score": 1.0,
                    "stepResults": [
                        {
                            "stepIdx": 0,
                            "conceptIds": [],
                            "correct": True,
                            "durationMs": 1500,
                        }
                    ],
                }
            ],
        },
        headers=_hdr(sub),
    )


def test_leaderboard_opt_in_default_skips_write(api_client) -> None:
    """Default settings → no leaderboard row written.
    After opting in + setting learning language → row exists.
    """
    client, user_id, _admin_id = api_client

    # 1. Default settings — do a lesson. Even without learning language set,
    #    the hook must skip because show_on_leaderboard defaults to False.
    resp = _submit_lesson(client, "dev|test-user", "lesson-1", uuid.uuid4().hex)
    assert resp.status_code == 200, resp.text
    accepted = resp.json()["results"][0]["accepted"]
    assert accepted is True

    # Inspect the leaderboard repo: nothing written.
    from app.db.provider import get_social_repo

    repo = get_social_repo()
    assert repo is not None

    import asyncio
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    iso = now.isocalendar()
    bucket = f"ja#{iso.year:04d}-W{iso.week:02d}"

    async def _check_no_entry() -> None:
        entry = await repo.get_user_leaderboard_entry(bucket, user_id)
        assert entry is None

    asyncio.get_event_loop().run_until_complete(_check_no_entry())

    # 2. Opt in + set learning language. Then submit another lesson.
    resp = client.patch(
        "/api/core/v1/users/me/settings",
        json={
            "social": {"show_on_leaderboard": True},
            "learning": {"learningLanguageId": "ja"},
        },
        headers=_hdr("dev|test-user"),
    )
    assert resp.status_code == 200, resp.text

    resp = _submit_lesson(client, "dev|test-user", "lesson-2", uuid.uuid4().hex)
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"][0]["accepted"] is True

    async def _check_has_entry() -> None:
        entry = await repo.get_user_leaderboard_entry(bucket, user_id)
        assert entry is not None
        assert entry["xp"] > 0
        assert entry["lessons"] >= 1

    asyncio.get_event_loop().run_until_complete(_check_has_entry())


# ── Public profile visibility ──────────────────────────────────────────────


def test_public_profile_visibility(api_client) -> None:
    """visibility=private → 404 to strangers. visibility=friends → 200 only to friends.
    visibility=public → 200 to anyone.
    """
    client, user_id, _admin_id = api_client
    me = client.get("/api/core/v1/users/me", headers=_hdr("dev|test-user")).json()
    my_username = me["username"]

    # Stranger user.
    stranger_sub = f"dev|stranger-{uuid.uuid4().hex[:6]}"
    _stranger_id, _stranger_username = _register_user(client, stranger_sub)

    # 1. Private — stranger gets 404.
    client.patch(
        "/api/core/v1/users/me/settings",
        json={"social": {"visibility": "private"}},
        headers=_hdr("dev|test-user"),
    )
    resp = client.get(
        f"/api/core/v1/social/profiles/{my_username}",
        headers=_hdr(stranger_sub),
    )
    assert resp.status_code == 404

    # Self can still see own profile.
    resp = client.get(
        f"/api/core/v1/social/profiles/{my_username}",
        headers=_hdr("dev|test-user"),
    )
    assert resp.status_code == 200
    assert resp.json()["friendship_status"] == "self"

    # 2. friends-only — stranger 404, friend 200.
    client.patch(
        "/api/core/v1/users/me/settings",
        json={"social": {"visibility": "friends"}},
        headers=_hdr("dev|test-user"),
    )
    resp = client.get(
        f"/api/core/v1/social/profiles/{my_username}",
        headers=_hdr(stranger_sub),
    )
    assert resp.status_code == 404

    # Make them friends — register a friend, exchange request + accept.
    friend_sub = f"dev|friend-{uuid.uuid4().hex[:6]}"
    friend_id, _ = _register_user(client, friend_sub)
    client.post(
        "/api/core/v1/social/friends/requests",
        json={"toUserId": friend_id},
        headers=_hdr("dev|test-user"),
    )
    client.post(
        f"/api/core/v1/social/friends/requests/{user_id}/accept",
        headers=_hdr(friend_sub),
    )
    resp = client.get(
        f"/api/core/v1/social/profiles/{my_username}",
        headers=_hdr(friend_sub),
    )
    assert resp.status_code == 200
    assert resp.json()["friendship_status"] == "friend"

    # 3. public — anyone 200.
    client.patch(
        "/api/core/v1/users/me/settings",
        json={"social": {"visibility": "public"}},
        headers=_hdr("dev|test-user"),
    )
    resp = client.get(
        f"/api/core/v1/social/profiles/{my_username}",
        headers=_hdr(stranger_sub),
    )
    assert resp.status_code == 200


# Silence unused-import warnings on Iterator (kept for parity with codebase style).
_unused: Iterator | None = None
_unused_pytest = pytest
