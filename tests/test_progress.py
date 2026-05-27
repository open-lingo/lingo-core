"""Progress API tests — verifies XP accumulates correctly across batches
and that re-pushing the same client attempt is idempotent (Fix 2 + Fix 3)."""

import uuid
from datetime import UTC, datetime


def _attempt(client_id: str, lesson_id: str = "lesson-001") -> dict:
    return {
        "clientAttemptId": client_id,
        "lessonId": lesson_id,
        "attemptedAt": datetime.now(UTC).isoformat(),
        "durationSec": 60,
        "passed": True,
        "score": 1.0,
        "stepResults": [
            {"stepIdx": 0, "conceptIds": ["c1"], "correct": True, "durationMs": 4000},
            {"stepIdx": 1, "conceptIds": ["c1"], "correct": True, "durationMs": 5000},
        ],
    }


def test_batch_xp_accumulates(api_client) -> None:
    """Submitting 5 attempts in one batch should yield 5x the per-attempt XP.

    Reproduces C2 (XP overwrite under read-modify-write per attempt). Before
    the fix, the second attempt would read the user row before the first
    attempt's update was committed in some backends; with the fix all five
    increments must land.
    """
    client, user_id, _ = api_client

    attempts = [_attempt(str(uuid.uuid4())) for _ in range(5)]
    body = {"attempts": attempts, "checkStreak": False}
    resp = client.post("/api/core/v1/progress/lessons/batch", json=body)
    assert resp.status_code == 200, resp.text
    results = resp.json()["results"]
    assert len(results) == 5
    assert all(r["accepted"] for r in results)
    expected_total_xp = sum(r["xpEarned"] for r in results)
    assert expected_total_xp > 0, "expected each attempt to earn xp"

    me = client.get("/api/core/v1/users/me").json()
    assert me["xp"] == expected_total_xp


def test_batch_collapses_to_one_user_update(api_client, monkeypatch) -> None:
    """Fix 2 — the batch endpoint must collapse N attempts into ONE
    user-row update, not N. Counts ``update_user`` calls on the live repo.
    """
    client, _user_id, _ = api_client

    from app.db import provider

    repo = provider.get_user_repo()
    call_count = {"n": 0}
    real_update = repo.update_user

    async def counting_update(uid, patch):
        call_count["n"] += 1
        return await real_update(uid, patch)

    monkeypatch.setattr(repo, "update_user", counting_update)

    attempts = [_attempt(str(uuid.uuid4())) for _ in range(4)]
    body = {"attempts": attempts, "checkStreak": False}
    resp = client.post("/api/core/v1/progress/lessons/batch", json=body)
    assert resp.status_code == 200, resp.text
    # Before the fix this would be 4. After the fix, exactly 1.
    assert call_count["n"] == 1, f"expected 1 update_user call, got {call_count['n']}"


def test_idempotent_retry(api_client) -> None:
    """Calling the batch endpoint twice with the same clientAttemptId returns
    the same attemptId, accepted=True, and does NOT double-credit XP.

    Reproduces C6 (orphan ATTEMPT row after partial put_attempt failure)."""
    client, user_id, _ = api_client

    cid = str(uuid.uuid4())
    body = {"attempts": [_attempt(cid)], "checkStreak": False}
    resp1 = client.post("/api/core/v1/progress/lessons/batch", json=body)
    assert resp1.status_code == 200, resp1.text
    r1 = resp1.json()["results"][0]
    assert r1["accepted"] is True
    first_attempt_id = r1["attemptId"]

    me1 = client.get("/api/core/v1/users/me").json()
    xp_after_first = me1["xp"]
    assert xp_after_first > 0

    # Re-submit the same payload — must be a no-op idempotent re-acknowledgement.
    resp2 = client.post("/api/core/v1/progress/lessons/batch", json=body)
    assert resp2.status_code == 200, resp2.text
    r2 = resp2.json()["results"][0]
    assert r2["accepted"] is True
    assert r2["attemptId"] == first_attempt_id

    me2 = client.get("/api/core/v1/users/me").json()
    assert me2["xp"] == xp_after_first, f"retry double-credited XP: {xp_after_first} → {me2['xp']}"
