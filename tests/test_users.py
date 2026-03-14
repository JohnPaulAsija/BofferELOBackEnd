"""
Integration tests for authenticated /users/* endpoints.

Requires a running Supabase instance reachable via test.env.
The reset_and_seed fixture (conftest.py) creates fresh test accounts
and provides session-scoped tokens for each role.
"""
import uuid

import pytest

from tests.conftest import _bearer, _decode_jwt_sub


# ---------------------------------------------------------------------------
# GET /users
# ---------------------------------------------------------------------------

async def test_list_users_no_auth(app_client):
    resp = await app_client.get("/users")
    assert resp.status_code == 422


async def test_list_users_bad_token(app_client):
    resp = await app_client.get("/users", headers=_bearer("garbage"))
    assert resp.status_code == 401


async def test_list_users_returns_200(app_client, user1_token):
    resp = await app_client.get("/users", headers=_bearer(user1_token))
    assert resp.status_code == 200


async def test_list_users_has_users_key(app_client, user1_token):
    resp = await app_client.get("/users", headers=_bearer(user1_token))
    data = resp.json()
    assert "users" in data
    assert isinstance(data["users"], list)


async def test_list_users_entry_shape(app_client, user1_token):
    resp = await app_client.get("/users", headers=_bearer(user1_token))
    users = resp.json()["users"]
    if not users:
        pytest.skip("no users returned")
    for u in users:
        assert isinstance(u["id"], str)
        assert isinstance(u["username"], str)


async def test_list_users_excludes_self(app_client, user1_token, user1_id):
    resp = await app_client.get("/users", headers=_bearer(user1_token))
    ids = [u["id"] for u in resp.json()["users"]]
    assert str(user1_id) not in ids


async def test_list_users_sorted_asc(app_client, user1_token):
    resp = await app_client.get("/users", headers=_bearer(user1_token))
    users = resp.json()["users"]
    if len(users) < 2:
        pytest.skip("need at least 2 users to test sort order")
    names = [u["username"] for u in users]
    assert names == sorted(names, key=str.lower)


async def test_list_users_excludes_incomplete_profiles(app_client, sync_supabase, user1_token):
    """Users created without a username in signup metadata (NULL username) must not appear."""
    if sync_supabase is None:
        pytest.skip("no test.env — integration tests only")
    user = sync_supabase.auth.admin.create_user(
        {"email": "incomplete_ul@test.com", "password": "TestPassword123!", "email_confirm": True}
    )
    incomplete_id = user.user.id
    try:
        resp = await app_client.get("/users", headers=_bearer(user1_token))
        assert resp.status_code == 200
        ids = [u["id"] for u in resp.json()["users"]]
        assert incomplete_id not in ids
    finally:
        sync_supabase.auth.admin.delete_user(incomplete_id)


# ---------------------------------------------------------------------------
# GET /users/me
# ---------------------------------------------------------------------------

async def test_me_no_auth(app_client):
    resp = await app_client.get("/users/me")
    assert resp.status_code == 422


async def test_me_bad_token(app_client):
    resp = await app_client.get("/users/me", headers=_bearer("garbage"))
    assert resp.status_code == 401


async def test_me_returns_200(app_client, user1_token):
    resp = await app_client.get("/users/me", headers=_bearer(user1_token))
    assert resp.status_code == 200


async def test_me_has_user_key(app_client, user1_token):
    resp = await app_client.get("/users/me", headers=_bearer(user1_token))
    assert "user" in resp.json()


async def test_me_shape(app_client, user1_token):
    resp = await app_client.get("/users/me", headers=_bearer(user1_token))
    user = resp.json()["user"]
    assert "id" in user
    assert "username" in user
    assert "role_id" in user
    # email may be present or null
    assert "email" in user


async def test_me_id_matches_token(app_client, user1_token):
    resp = await app_client.get("/users/me", headers=_bearer(user1_token))
    user = resp.json()["user"]
    assert user["id"] == _decode_jwt_sub(user1_token)


async def test_me_role_id_is_integer(app_client, user1_token):
    resp = await app_client.get("/users/me", headers=_bearer(user1_token))
    assert isinstance(resp.json()["user"]["role_id"], int)


async def test_me_admin_has_elevated_role(app_client, admin_token):
    resp = await app_client.get("/users/me", headers=_bearer(admin_token))
    assert resp.json()["user"]["role_id"] >= 2


# ---------------------------------------------------------------------------
# GET /users/me/matches
# ---------------------------------------------------------------------------

async def test_me_matches_no_auth(app_client):
    resp = await app_client.get("/users/me/matches")
    assert resp.status_code == 422


async def test_me_matches_bad_token(app_client):
    resp = await app_client.get("/users/me/matches", headers=_bearer("garbage"))
    assert resp.status_code == 401


async def test_me_matches_returns_200(app_client, user1_token):
    resp = await app_client.get("/users/me/matches", headers=_bearer(user1_token))
    assert resp.status_code == 200


async def test_me_matches_has_both_keys(app_client, user1_token):
    resp = await app_client.get("/users/me/matches", headers=_bearer(user1_token))
    data = resp.json()
    assert "confirmed" in data
    assert "unconfirmed" in data
    assert isinstance(data["confirmed"], list)
    assert isinstance(data["unconfirmed"], list)


async def test_me_matches_confirmed_sorted(app_client, user1_token):
    resp = await app_client.get("/users/me/matches", headers=_bearer(user1_token))
    confirmed = resp.json()["confirmed"]
    if len(confirmed) < 2:
        pytest.skip("need at least 2 confirmed matches to test sort order")
    timestamps = [m["confirmedAt"] for m in confirmed]
    assert timestamps == sorted(timestamps, reverse=True)


async def test_me_matches_unconfirmed_sorted(app_client, user1_token):
    resp = await app_client.get("/users/me/matches", headers=_bearer(user1_token))
    unconfirmed = resp.json()["unconfirmed"]
    if len(unconfirmed) < 2:
        pytest.skip("need at least 2 unconfirmed matches to test sort order")
    timestamps = [m["reportedAt"] for m in unconfirmed]
    assert timestamps == sorted(timestamps, reverse=True)


async def test_me_matches_only_own(app_client, user1_token, user1_id):
    resp = await app_client.get("/users/me/matches", headers=_bearer(user1_token))
    data = resp.json()
    all_matches = data["confirmed"] + data["unconfirmed"]
    if not all_matches:
        pytest.skip("no matches to check ownership")
    uid = str(user1_id)
    for m in all_matches:
        assert m["winnerId"] == uid or m["loserId"] == uid


async def test_me_matches_confirmed_have_confirmed_at(app_client, user1_token):
    resp = await app_client.get("/users/me/matches", headers=_bearer(user1_token))
    confirmed = resp.json()["confirmed"]
    if not confirmed:
        pytest.skip("no confirmed matches")
    for m in confirmed:
        assert m["confirmedAt"] is not None


async def test_me_matches_unconfirmed_have_no_confirmed_at(app_client, user1_token):
    resp = await app_client.get("/users/me/matches", headers=_bearer(user1_token))
    unconfirmed = resp.json()["unconfirmed"]
    if not unconfirmed:
        pytest.skip("no unconfirmed matches")
    for m in unconfirmed:
        assert m["confirmedAt"] is None


async def test_me_matches_no_rejected(app_client, user1_token):
    resp = await app_client.get("/users/me/matches", headers=_bearer(user1_token))
    data = resp.json()
    for m in data["confirmed"] + data["unconfirmed"]:
        assert m.get("rejectedAt") is None


# ---------------------------------------------------------------------------
# GET /users/me/matches/unconfirmed
# ---------------------------------------------------------------------------

async def test_me_unconfirmed_no_auth(app_client):
    resp = await app_client.get("/users/me/matches/unconfirmed")
    assert resp.status_code == 422


async def test_me_unconfirmed_bad_token(app_client):
    resp = await app_client.get(
        "/users/me/matches/unconfirmed", headers=_bearer("garbage")
    )
    assert resp.status_code == 401


async def test_me_unconfirmed_returns_200(app_client, user1_token):
    resp = await app_client.get(
        "/users/me/matches/unconfirmed", headers=_bearer(user1_token)
    )
    assert resp.status_code == 200


async def test_me_unconfirmed_has_key(app_client, user1_token):
    resp = await app_client.get(
        "/users/me/matches/unconfirmed", headers=_bearer(user1_token)
    )
    data = resp.json()
    assert "unconfirmed_matches" in data
    assert isinstance(data["unconfirmed_matches"], list)


async def test_me_unconfirmed_only_own(app_client, user1_token, user1_id):
    resp = await app_client.get(
        "/users/me/matches/unconfirmed", headers=_bearer(user1_token)
    )
    matches = resp.json()["unconfirmed_matches"]
    if not matches:
        pytest.skip("no unconfirmed matches")
    uid = str(user1_id)
    for m in matches:
        assert m["winnerId"] == uid or m["loserId"] == uid


async def test_me_unconfirmed_no_confirmed_at(app_client, user1_token):
    resp = await app_client.get(
        "/users/me/matches/unconfirmed", headers=_bearer(user1_token)
    )
    for m in resp.json()["unconfirmed_matches"]:
        assert m["confirmedAt"] is None


async def test_me_unconfirmed_no_rejected_at(app_client, user1_token):
    resp = await app_client.get(
        "/users/me/matches/unconfirmed", headers=_bearer(user1_token)
    )
    for m in resp.json()["unconfirmed_matches"]:
        assert m.get("rejectedAt") is None


# ---------------------------------------------------------------------------
# PATCH /users/me/preferences
# ---------------------------------------------------------------------------

async def test_prefs_no_auth(app_client):
    resp = await app_client.patch("/users/me/preferences", json={"gender": "Male"})
    assert resp.status_code == 422


async def test_prefs_bad_token(app_client):
    resp = await app_client.patch(
        "/users/me/preferences",
        json={"gender": "Male"},
        headers=_bearer("garbage"),
    )
    assert resp.status_code == 401


async def test_prefs_invalid_gender(app_client, user1_token):
    resp = await app_client.patch(
        "/users/me/preferences",
        json={"gender": "invalid"},
        headers=_bearer(user1_token),
    )
    assert resp.status_code == 422


async def test_prefs_invalid_game(app_client, user1_token):
    resp = await app_client.patch(
        "/users/me/preferences",
        json={"preferred_game": "invalid"},
        headers=_bearer(user1_token),
    )
    assert resp.status_code == 422


async def test_prefs_valid_update(app_client, user1_token):
    # Fetch valid options first
    opts = await app_client.get("/options")
    options = opts.json()
    gender = options["genders"][0]
    game = options["games"][0]

    resp = await app_client.patch(
        "/users/me/preferences",
        json={"gender": gender, "preferred_game": game},
        headers=_bearer(user1_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["gender"] == gender
    assert data["preferred_game"] == game


async def test_prefs_null_clears_field(app_client, user1_token):
    # First set a value
    opts = await app_client.get("/options")
    gender = opts.json()["genders"][0]
    await app_client.patch(
        "/users/me/preferences",
        json={"gender": gender},
        headers=_bearer(user1_token),
    )

    # Now clear it
    resp = await app_client.patch(
        "/users/me/preferences",
        json={"gender": None},
        headers=_bearer(user1_token),
    )
    assert resp.status_code == 200
    assert resp.json()["gender"] is None


# ---------------------------------------------------------------------------
# PATCH /users/{user_id}/preferences
# ---------------------------------------------------------------------------

async def test_update_prefs_regular_user_forbidden(app_client, user1_token, user2_id):
    resp = await app_client.patch(
        f"/users/{user2_id}/preferences",
        json={"gender": "Male"},
        headers=_bearer(user1_token),
    )
    assert resp.status_code == 403


async def test_update_prefs_admin_forbidden(app_client, admin_token, user1_id):
    resp = await app_client.patch(
        f"/users/{user1_id}/preferences",
        json={"gender": "Male"},
        headers=_bearer(admin_token),
    )
    assert resp.status_code == 403


async def test_update_prefs_superadmin_unknown_user(app_client, super_admin_token):
    fake_id = str(uuid.uuid4())
    resp = await app_client.patch(
        f"/users/{fake_id}/preferences",
        json={"gender": "Male"},
        headers=_bearer(super_admin_token),
    )
    assert resp.status_code == 404


async def test_update_prefs_superadmin_success(app_client, super_admin_token, user1_id):
    opts = await app_client.get("/options")
    gender = opts.json()["genders"][0]

    resp = await app_client.patch(
        f"/users/{user1_id}/preferences",
        json={"gender": gender},
        headers=_bearer(super_admin_token),
    )
    assert resp.status_code == 200
    assert resp.json()["gender"] == gender
