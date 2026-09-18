"""First-touch auto-provisioning (FIRSTRUN lane, 2026-09-18).

The bug this closes: a brand-new signed-in user's client fires its boot
wave — GET /boot, GET /progress/me (x5), GET /quests (x2), GET /users/me
(x2), /users/me/subscriptions, /users/me/settings, /srs/state,
/progress/me/unlocks, POST /progress/me/touch, /decks/admin,
/social/profiles/<name>, /users/discover — ALL of which depend on
`get_registered_user` and used to 404 until the client's separate
`POST /users/me` registration form was submitted (real evidence: ~26s,
15+ 404s). `get_registered_user` now auto-provisions a placeholder row on
first touch (`app/auth/dependencies.py::_provision_user`) instead of
404ing, and `register_user` (`POST /users/me`) claims that row in place
when the caller later picks a real username, instead of 409ing.
"""

BOOT = "/api/core/v1/boot"
ME = "/api/core/v1/users/me"

# The exact endpoint set from the FIRSTRUN evidence (CloudWatch
# `lingo.access`, user hash f3fd3518, platform=android) that 404'd for a
# brand-new user before this fix, minus /decks/admin (a genuinely
# admin-only route — a non-admin caller now gets 403 there, not 404;
# see the lane report for why that's a separate, pre-existing bug) and
# /social/profiles/<name> (targets a DIFFERENT user's profile by
# username, not this caller's own identity, so it isn't exercised here).
STORM_ENDPOINTS: list[tuple[str, str]] = [
    ("GET", "/api/core/v1/boot"),
    ("GET", "/api/core/v1/progress/me"),
    ("GET", "/api/core/v1/users/me"),
    ("GET", "/api/core/v1/users/me/subscriptions"),
    ("GET", "/api/core/v1/users/me/settings"),
    ("GET", "/api/core/v1/srs/state"),
    ("GET", "/api/core/v1/progress/me/unlocks"),
    ("POST", "/api/core/v1/progress/me/touch"),
    ("GET", "/api/core/v1/quests"),
    ("GET", "/api/core/v1/users/discover"),
]


def test_first_login_storm_produces_zero_404s(api_client) -> None:
    """Replay the FIRSTRUN evidence's endpoint set, in order, for a
    caller that has never touched the API before. None of them may 404 —
    that 404 was the entire bug."""
    client, _, _ = api_client
    headers = {"X-Dev-User": "dev|firstrun-storm"}

    statuses: dict[str, int] = {}
    for method, path in STORM_ENDPOINTS:
        resp = client.request(method, path, headers=headers)
        statuses[f"{method} {path}"] = resp.status_code

    not_found = {k: v for k, v in statuses.items() if v == 404}
    assert not_found == {}, f"first-login 404s: {not_found} (full: {statuses})"


def test_register_claims_a_provisioned_placeholder(api_client) -> None:
    """A user who was auto-provisioned (e.g. their client's boot wave hit
    /boot before they finished the register form) can still register with
    their chosen username — same id, no 409, and progress/XP accrued
    under the placeholder (there is none here, but the row identity)
    survives the claim."""
    client, _, _ = api_client
    sub = "dev|claims-placeholder"
    headers = {"X-Dev-User": sub}

    provisioned = client.get(BOOT, headers=headers).json()["user"]
    assert provisioned["display_name"] == ""

    resp = client.post(
        ME,
        json={"username": "riley_real", "display_name": "Riley"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    claimed = resp.json()
    assert claimed["id"] == provisioned["id"]
    assert claimed["username"] == "riley_real"
    assert claimed["display_name"] == "Riley"

    # And it stuck — a fresh GET sees the claimed identity, not the
    # placeholder.
    me = client.get(ME, headers=headers).json()
    assert me["username"] == "riley_real"
    assert me["display_name"] == "Riley"


def test_register_still_409s_once_fully_registered(api_client) -> None:
    """Claiming is a ONE-TIME transition. A second POST /users/me for an
    already-registered (non-placeholder) identity still 409s — the
    pre-existing "already registered" contract is unchanged."""
    client, _, _ = api_client
    sub = "dev|already-registered"
    headers = {"X-Dev-User": sub}

    first = client.post(
        ME,
        json={"username": "first_pick", "display_name": "First"},
        headers=headers,
    )
    assert first.status_code == 201, first.text

    second = client.post(
        ME,
        json={"username": "second_pick", "display_name": "Second"},
        headers=headers,
    )
    assert second.status_code == 409


def test_register_claim_respects_username_uniqueness(api_client) -> None:
    """Claiming a placeholder still checks the username isn't taken by
    someone ELSE — only self-claims bypass the "already registered"
    check, not the uniqueness one."""
    client, _, _ = api_client

    other = client.post(
        ME,
        json={"username": "taken_name", "display_name": "Other"},
        headers={"X-Dev-User": "dev|other-user"},
    )
    assert other.status_code == 201, other.text

    headers = {"X-Dev-User": "dev|wants-taken-name"}
    client.get(BOOT, headers=headers)  # provisions the placeholder
    resp = client.post(
        ME,
        json={"username": "taken_name", "display_name": "Wants It"},
        headers=headers,
    )
    assert resp.status_code == 409


def test_concurrent_first_touch_creates_exactly_one_row(api_client) -> None:
    """The scenario that broke a naive 'check-then-create': two requests
    for the SAME not-yet-registered sub landing back to back (boot +
    users/me firing within ms of each other on a real first login) must
    converge on one row, not two."""
    client, _, _ = api_client
    sub = "dev|racing-first-touch"
    headers = {"X-Dev-User": sub}

    user_ids = {
        client.get(BOOT, headers=headers).json()["user"]["id"],
        client.get(ME, headers=headers).json()["id"],
    }
    assert len(user_ids) == 1

    admin_list = client.get(
        "/api/core/v1/admin/users",
        headers={"X-Dev-User": "dev|admin-user"},
    )
    if admin_list.status_code == 200:
        matches = [u for u in admin_list.json()["items"] if u["auth0_id"] == sub]
        assert len(matches) == 1, f"expected exactly one row for {sub}, found {len(matches)}"
