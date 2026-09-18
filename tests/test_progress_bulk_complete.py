"""POST /progress/lessons/bulk-complete — the ids-only sibling of
lessons/batch, added 2026-09-18 after a 491-row test-out batch sat queued
on a client for weeks without ever reaching the server. Covers idempotency
(whole-op via clientOpId, per-lesson via first-wins), partial overlap with
already-complete lessons, the 1000-id cap (413), test-out exemptions
(no XP/lingots/streak/day-rollup touched), and the exact phone scenario
(491 ids in one request).
"""

import uuid
from datetime import UTC, datetime


def _body(n: int, *, source: str = "test_out", prefix: str = "ja-m1-l") -> dict:
    return {
        "lang": "ja",
        "source": source,
        "lessonIds": [f"{prefix}{i}" for i in range(n)],
        "completedAt": datetime.now(UTC).isoformat(),
        "clientOpId": str(uuid.uuid4()),
    }


def test_bulk_complete_marks_every_lesson_and_does_not_touch_xp(api_client) -> None:
    client, user_id, _ = api_client
    body = _body(5)

    resp = client.post("/api/core/v1/progress/lessons/bulk-complete", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data == {"accepted": 5, "alreadyComplete": 0, "total": 5}

    # XP-neutral — the user row must be completely untouched (not just
    # "xp unchanged", the whole batch handler's update_user path never runs).
    me = client.get("/api/core/v1/users/me").json()
    assert me["xp"] == 0
    assert me["lingots"] == 0
    assert me.get("streak", 0) == 0

    summary = client.get("/api/core/v1/progress/me").json()
    rollups = summary["lessons"]
    completed_ids = {r["lessonId"] for r in rollups if r["firstPassedAt"]}
    assert completed_ids == set(body["lessonIds"])

    # Day rollup exempt — no day-activity entry shows any lessons completed.
    assert all(d.get("lessonsCompleted", 0) == 0 for d in summary["last30days"])


def test_bulk_complete_the_exact_phone_scenario_491_ids_one_request(api_client) -> None:
    """Spencer's phone: 491 test-out lessons, stuck for weeks as 491
    individually-failable rows. This is the fix's whole point — one request."""
    client, user_id, _ = api_client
    body = _body(491)

    resp = client.post("/api/core/v1/progress/lessons/bulk-complete", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 491
    assert data["accepted"] == 491
    assert data["alreadyComplete"] == 0

    rollups = client.get("/api/core/v1/progress/me").json()["lessons"]
    assert sum(1 for r in rollups if r["firstPassedAt"]) == 491


def test_bulk_complete_idempotent_on_client_op_id_same_counts_no_dup_rows(api_client) -> None:
    client, user_id, _ = api_client
    body = _body(10)

    first = client.post("/api/core/v1/progress/lessons/bulk-complete", json=body)
    second = client.post("/api/core/v1/progress/lessons/bulk-complete", json=body)
    assert first.json() == second.json() == {"accepted": 10, "alreadyComplete": 0, "total": 10}

    rollups = client.get("/api/core/v1/progress/me").json()["lessons"]
    # Exactly 10 rows — a naive re-processing would not duplicate rows either
    # (update_lesson_rollup is itself idempotent per lesson), but this pins
    # the whole-op cache path specifically: attemptCount must NOT have been
    # incremented a second time by a replayed op.
    touched = [r for r in rollups if r["lessonId"] in set(body["lessonIds"])]
    assert len(touched) == 10
    assert all(r["attemptCount"] == 1 for r in touched)


def test_bulk_complete_partial_overlap_reports_already_complete(api_client) -> None:
    client, user_id, _ = api_client
    first = _body(5, prefix="ja-m2-l")
    resp1 = client.post("/api/core/v1/progress/lessons/bulk-complete", json=first)
    assert resp1.json() == {"accepted": 5, "alreadyComplete": 0, "total": 5}

    # A second, DIFFERENT op (different clientOpId — e.g. a later reconcile
    # pass) that overlaps 3 of the same lessons plus 2 new ones.
    second = {
        "lang": "ja",
        "source": "test_out",
        "lessonIds": first["lessonIds"][2:5] + ["ja-m2-l5", "ja-m2-l6"],
        "completedAt": datetime.now(UTC).isoformat(),
        "clientOpId": str(uuid.uuid4()),
    }
    resp2 = client.post("/api/core/v1/progress/lessons/bulk-complete", json=second)
    assert resp2.status_code == 200, resp2.text
    assert resp2.json() == {"accepted": 2, "alreadyComplete": 3, "total": 5}


def test_bulk_complete_a_lesson_already_completed_via_lessons_batch_counts_as_already_complete(
    api_client,
) -> None:
    """Per-lesson idempotency isn't scoped to bulk-complete's own history —
    a lesson finished for real (lessons/batch) before a reconcile pass tries
    to bulk-complete it must be reported alreadyComplete, never double-applied."""
    client, user_id, _ = api_client
    real_attempt = {
        "clientAttemptId": str(uuid.uuid4()),
        "lessonId": "ja-m1-l3",
        "attemptedAt": datetime.now(UTC).isoformat(),
        "durationSec": 60,
        "passed": True,
        "score": 1.0,
        "stepResults": [{"stepIdx": 0, "conceptIds": [], "correct": True}],
    }
    resp = client.post(
        "/api/core/v1/progress/lessons/batch",
        json={"attempts": [real_attempt], "checkStreak": False},
    )
    assert resp.status_code == 200, resp.text
    real_xp = resp.json()["results"][0]["xpEarned"]
    assert real_xp > 0

    body = _body(3, prefix="ja-m1-l")  # includes ja-m1-l0, l1, l2 — not l3
    body["lessonIds"].append("ja-m1-l3")
    resp2 = client.post("/api/core/v1/progress/lessons/bulk-complete", json=body)
    assert resp2.json() == {"accepted": 3, "alreadyComplete": 1, "total": 4}

    # The real attempt's XP must be untouched by the bulk pass.
    me = client.get("/api/core/v1/users/me").json()
    assert me["xp"] == real_xp


def test_bulk_complete_caps_at_1000_with_413(api_client) -> None:
    client, user_id, _ = api_client
    body = _body(1001)
    resp = client.post("/api/core/v1/progress/lessons/bulk-complete", json=body)
    assert resp.status_code == 413, resp.text


def test_bulk_complete_at_exactly_1000_succeeds(api_client) -> None:
    client, user_id, _ = api_client
    body = _body(1000)
    resp = client.post("/api/core/v1/progress/lessons/bulk-complete", json=body)
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 1000


def test_bulk_complete_placement_source_also_xp_neutral(api_client) -> None:
    client, user_id, _ = api_client
    body = _body(4, source="placement")
    resp = client.post("/api/core/v1/progress/lessons/bulk-complete", json=body)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"accepted": 4, "alreadyComplete": 0, "total": 4}
    me = client.get("/api/core/v1/users/me").json()
    assert me["xp"] == 0


def test_bulk_complete_rejects_empty_lesson_ids(api_client) -> None:
    client, user_id, _ = api_client
    body = _body(0)
    resp = client.post("/api/core/v1/progress/lessons/bulk-complete", json=body)
    assert resp.status_code == 422, resp.text
