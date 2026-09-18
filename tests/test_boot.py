"""GET /api/core/v1/boot — the batched boot read.

The contract that matters: /boot returns exactly what the individual
endpoints return (it calls their handlers), and — since the FIRSTRUN lane
(2026-09-18) — a signed-in-but-never-registered caller is auto-provisioned
on this very request instead of 404ing. See
``app/auth/dependencies.py::_provision_user`` and the FIRSTRUN evidence:
a brand-new user's boot wave used to fire GET /boot, GET /progress/me
(x5), GET /quests (x2), GET /users/me (x2), /users/me/subscriptions,
/users/me/settings, /srs/state, /progress/me/unlocks, POST
/progress/me/touch — ALL 404 — for ~26s until the client's separate
POST /users/me registration form was submitted.
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


def test_boot_provisions_unregistered_user(api_client) -> None:
    """A signed-in-but-never-registered caller is auto-provisioned on this
    very request and gets a full 200 payload — no 404, no partial payload,
    and every section resolves (this is the fix for the FIRSTRUN storm)."""
    client, _, _ = api_client

    resp = client.get(BOOT, headers={"X-Dev-User": "dev|never-registered"})
    assert resp.status_code == 200
    boot = resp.json()
    assert boot["user"]["auth0_id"] == "dev|never-registered"
    # Placeholder sentinel: no real registration can ever produce this —
    # `UserCreate`/`MeUpdate` both require `min_length=1` on display_name.
    assert boot["user"]["display_name"] == ""
    assert boot["user"]["username"]
    # The other batched sections all resolve too, not just `user`.
    assert boot["settings"] is not None
    assert boot["progress"] is not None
    assert boot["unlocks"] is not None
    assert boot["srs"] is not None


def test_boot_provisioning_is_idempotent(api_client) -> None:
    """A second /boot for the same not-yet-registered sub must NOT create a
    second row — same id, same username, both times (the race this guards:
    the client's boot wave fires this endpoint and GET /users/me within ms
    of each other on a real first login)."""
    client, _, _ = api_client
    headers = {"X-Dev-User": "dev|never-registered-2"}

    first = client.get(BOOT, headers=headers).json()
    second = client.get(BOOT, headers=headers).json()
    assert first["user"]["id"] == second["user"]["id"]
    assert first["user"]["username"] == second["user"]["username"]

    admin_list = client.get(
        "/api/core/v1/admin/users",
        headers={"X-Dev-User": "dev|admin-user"},
    )
    if admin_list.status_code == 200:
        matches = [
            u for u in admin_list.json()["items"] if u["auth0_id"] == "dev|never-registered-2"
        ]
        assert len(matches) == 1


def test_boot_provisioned_user_settings_and_quests_work(api_client) -> None:
    """Once provisioned via /boot, the individual routes /boot batches
    (settings, quests) resolve for that same identity too — the whole
    point is that NOTHING downstream of registration still 404s."""
    client, _, _ = api_client
    headers = {"X-Dev-User": "dev|never-registered-3"}

    boot_resp = client.get(BOOT, headers=headers)
    assert boot_resp.status_code == 200

    settings_resp = client.get("/api/core/v1/users/me/settings", headers=headers)
    assert settings_resp.status_code == 200

    quests_resp = client.get("/api/core/v1/quests", headers=headers)
    assert quests_resp.status_code in (200, 503)  # 503 only if the quest repo isn't wired in this env
    assert quests_resp.status_code != 404

    me_resp = client.get("/api/core/v1/users/me", headers=headers)
    assert me_resp.status_code == 200
    assert me_resp.json()["id"] == boot_resp.json()["user"]["id"]


def test_boot_still_falls_through_on_real_boot_error(api_client, monkeypatch) -> None:
    """The bootCache client fallback ('/boot failed → hit individual
    endpoints') is for REAL errors (5xx, network) — verify a genuine
    downstream failure still propagates as an error, not a silent 200,
    i.e. the provisioning change didn't swallow real exceptions along
    with the old 404."""
    client, _, _ = api_client

    async def _boom(*args, **kwargs):
        raise RuntimeError("simulated downstream failure")

    # `app/boot/router.py` imports the handler by name
    # (`from app.srs.router import get_state`), so the patch target is
    # boot's own module reference, not srs.router's.
    from app.boot import router as boot_router

    monkeypatch.setattr(boot_router, "get_state", _boom)
    # TestClient re-raises unhandled server exceptions by default rather
    # than turning them into a 500 response.
    import pytest

    with pytest.raises(RuntimeError, match="simulated downstream failure"):
        client.get(BOOT)
