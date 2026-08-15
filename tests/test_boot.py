"""GET /api/core/v1/boot — the batched boot read.

The contract that matters: /boot returns exactly what the individual
endpoints return (it calls their handlers), and a missing user record 404s
so the client's create-user fallback still runs.
"""

BOOT = "/api/core/v1/boot"


def test_boot_matches_individual_endpoints(api_client) -> None:
    client, user_id, _ = api_client

    resp = client.get(BOOT)
    assert resp.status_code == 200
    boot = resp.json()

    # Section-by-section parity with the endpoints it batches.
    assert boot["user"] == client.get("/api/core/v1/users/me").json()
    assert boot["settings"] == client.get("/api/core/v1/users/me/settings").json()
    assert boot["progress"] == client.get("/api/core/v1/progress/me").json()
    assert boot["unlocks"] == client.get("/api/core/v1/progress/me/unlocks").json()
    assert boot["srs"] == client.get("/api/core/v1/srs/state").json()
    # touch is a read (streak is never bumped here) — shape parity only:
    # both carry the same user stats; staleConceptIds may legitimately move.
    touch = client.post("/api/core/v1/progress/me/touch").json()
    assert boot["touch"]["user"] == touch["user"]
    assert boot["touch"]["streakUpdated"] is False


def test_boot_quests_and_subscriptions_sections(api_client) -> None:
    client, _, _ = api_client

    boot = client.get(BOOT).json()
    quests = client.get("/api/core/v1/quests")
    subs = client.get("/api/core/v1/users/me/subscriptions")

    # Best-effort sections: present iff their endpoint works in this env.
    if quests.status_code == 200:
        assert boot["quests"] is not None
        assert {q["id"] for q in boot["quests"]["items"]} == {q["id"] for q in quests.json()["items"]}
    else:
        assert boot["quests"] is None
    if subs.status_code == 200:
        assert boot["subscriptions"] == subs.json()
    else:
        assert boot["subscriptions"] is None


def test_boot_404_for_unregistered_user(api_client) -> None:
    """A signed-in-but-never-registered user must get 404 (not a partial
    payload) so the client keeps its existing create-user flow."""
    client, _, _ = api_client

    resp = client.get(BOOT, headers={"X-Dev-User": "dev|never-registered"})
    assert resp.status_code == 404
