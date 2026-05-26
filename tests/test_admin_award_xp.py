"""Admin "Award XP" endpoint — manual XP grants for testing leaderboards.

Verifies:
  - Award lands on the target user row + reflects in /users/me.
  - Level recomputes from the new XP.
  - Negative amounts decrement (and clamp at zero).
  - amount=0 is rejected.
  - 404 on unknown user.
  - Non-admin callers get 403.
  - The leaderboard mirror call runs when the user opts in (and is
    swallowed if the social repo doesn't expose the method — which it
    doesn't today; this just confirms the success path doesn't crash).
"""


def _admin_headers() -> dict[str, str]:
    return {"X-Dev-User": "dev|admin-user"}


def test_award_xp_updates_user(api_client, monkeypatch) -> None:
    client, user_id, admin_user_id = api_client

    from app.config import settings as cfg

    monkeypatch.setattr(cfg, "ADMIN_USER_IDS", [admin_user_id])

    resp = client.post(
        f"/api/core/v1/admin/users/{user_id}/award-xp",
        json={"amount": 250, "reason": "leaderboard test"},
        headers=_admin_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user_id"] == user_id
    assert body["xp"] == 250
    assert body["awarded"] == 250
    assert body["reason"] == "leaderboard test"
    # Linear curve (XP_PER_LEVEL=500): xp=250 → level=1.
    assert body["level"] == 1

    me = client.get("/api/core/v1/users/me").json()
    assert me["xp"] == 250
    assert me["level"] == 1


def test_award_xp_recomputes_level(api_client, monkeypatch) -> None:
    client, user_id, admin_user_id = api_client

    from app.config import settings as cfg

    monkeypatch.setattr(cfg, "ADMIN_USER_IDS", [admin_user_id])

    # Award enough XP to clear a level threshold (500 → level 2).
    resp = client.post(
        f"/api/core/v1/admin/users/{user_id}/award-xp",
        json={"amount": 750, "reason": ""},
        headers=_admin_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["xp"] == 750
    assert body["level"] >= 2


def test_award_xp_negative_decrements_and_clamps(api_client, monkeypatch) -> None:
    client, user_id, admin_user_id = api_client

    from app.config import settings as cfg

    monkeypatch.setattr(cfg, "ADMIN_USER_IDS", [admin_user_id])

    # Seed an XP balance first.
    client.post(
        f"/api/core/v1/admin/users/{user_id}/award-xp",
        json={"amount": 100, "reason": ""},
        headers=_admin_headers(),
    )

    resp = client.post(
        f"/api/core/v1/admin/users/{user_id}/award-xp",
        json={"amount": -50, "reason": "rollback"},
        headers=_admin_headers(),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["xp"] == 50

    # Over-decrement clamps at zero, not negative.
    resp2 = client.post(
        f"/api/core/v1/admin/users/{user_id}/award-xp",
        json={"amount": -9999, "reason": "wipe"},
        headers=_admin_headers(),
    )
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["xp"] == 0


def test_award_xp_zero_amount_rejected(api_client, monkeypatch) -> None:
    client, user_id, admin_user_id = api_client

    from app.config import settings as cfg

    monkeypatch.setattr(cfg, "ADMIN_USER_IDS", [admin_user_id])

    resp = client.post(
        f"/api/core/v1/admin/users/{user_id}/award-xp",
        json={"amount": 0, "reason": ""},
        headers=_admin_headers(),
    )
    assert resp.status_code == 400, resp.text


def test_award_xp_unknown_user_404(api_client, monkeypatch) -> None:
    client, _user_id, admin_user_id = api_client

    from app.config import settings as cfg

    monkeypatch.setattr(cfg, "ADMIN_USER_IDS", [admin_user_id])

    resp = client.post(
        "/api/core/v1/admin/users/00000000-0000-0000-0000-000000000000/award-xp",
        json={"amount": 10, "reason": ""},
        headers=_admin_headers(),
    )
    assert resp.status_code == 404, resp.text


def test_non_admin_blocked(api_client) -> None:
    client, user_id, _admin_user_id = api_client

    # Default identity is the dev user, NOT in the allow-list, so 403 is
    # the expected response from require_admin.
    resp = client.post(
        f"/api/core/v1/admin/users/{user_id}/award-xp",
        json={"amount": 10, "reason": ""},
    )
    assert resp.status_code == 403, resp.text


def test_award_xp_opted_in_does_not_crash(api_client, monkeypatch) -> None:
    """Opting the user into the leaderboard exercises the best-effort
    leaderboard mirror. The Sqlite social repo doesn't implement
    ``add_xp_to_leaderboard`` today, so the endpoint must still 200 (the
    call is swallowed in a try/except)."""
    client, user_id, admin_user_id = api_client

    from app.config import settings as cfg

    monkeypatch.setattr(cfg, "ADMIN_USER_IDS", [admin_user_id])

    # Set the show_on_leaderboard flag + learning language in settings.
    settings_resp = client.patch(
        "/api/core/v1/users/me/settings",
        json={
            "social": {"show_on_leaderboard": True},
            "learning": {"learningLanguageId": "es"},
        },
    )
    assert settings_resp.status_code in (200, 204), settings_resp.text

    resp = client.post(
        f"/api/core/v1/admin/users/{user_id}/award-xp",
        json={"amount": 42, "reason": "leaderboard opt-in"},
        headers=_admin_headers(),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["awarded"] == 42
