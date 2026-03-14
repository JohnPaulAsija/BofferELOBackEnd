"""
Integration tests for account management endpoints:
- PATCH /users/me/username
- PATCH /users/{user_id}/username
- PATCH /users/me/email
- PATCH /users/{user_id}/email
- DELETE /users/me
- DELETE /users/{user_id}

Requires a running Supabase instance reachable via test.env.
Destructive tests (DELETE) use sacrificial users — never session-scoped fixtures.
"""
import uuid
from datetime import datetime, timezone

import pytest

from tests.conftest import _bearer
from helpers import DELETED_USER_SENTINEL_ID


def _create_sacrificial_user(sync_supabase, email_prefix="sacrifice"):
    """Create a test user with a complete profile and return (user_id, token, username)."""
    from initialize import create_client
    email = f"{email_prefix}_{uuid.uuid4().hex[:8]}@test.com"
    password = "TestPassword123!"
    user_resp = sync_supabase.auth.admin.create_user(
        {"email": email, "password": password, "email_confirm": True}
    )
    uid = user_resp.user.id
    username = f"sac_{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()
    sync_supabase.from_("profiles").update({
        "username": username, "elo": 1000, "wins": 0, "losses": 0,
        "termsAcceptedAt": now,
    }).eq("id", uid).execute()
    tmp = create_client()
    sign_in = tmp.auth.sign_in_with_password({"email": email, "password": password})
    return uid, sign_in.session.access_token, username


# ---------------------------------------------------------------------------
# PATCH /users/me/username
# ---------------------------------------------------------------------------

async def test_change_username_no_auth(app_client):
    resp = await app_client.patch("/users/me/username", json={"username": "newname"})
    assert resp.status_code == 422


async def test_change_username_bad_token(app_client):
    resp = await app_client.patch(
        "/users/me/username", json={"username": "newname"}, headers=_bearer("garbage")
    )
    assert resp.status_code == 401


async def test_change_username_too_short(app_client, user1_token):
    resp = await app_client.patch(
        "/users/me/username", json={"username": "ab"}, headers=_bearer(user1_token)
    )
    assert resp.status_code == 422


async def test_change_username_invalid_chars(app_client, user1_token):
    resp = await app_client.patch(
        "/users/me/username", json={"username": "bad name!"}, headers=_bearer(user1_token)
    )
    assert resp.status_code == 422


async def test_change_username_success(app_client, user1_token):
    # Get current username to restore later
    me = await app_client.get("/users/me", headers=_bearer(user1_token))
    original = me.json()["user"]["username"]

    new_name = f"renamed_{uuid.uuid4().hex[:8]}"
    resp = await app_client.patch(
        "/users/me/username", json={"username": new_name}, headers=_bearer(user1_token)
    )
    assert resp.status_code == 200
    assert resp.json()["username"] == new_name

    # Restore original username
    restore = await app_client.patch(
        "/users/me/username", json={"username": original}, headers=_bearer(user1_token)
    )
    assert restore.status_code == 200


async def test_change_username_taken(app_client, user1_token, user2_id):
    # Get user2's username
    resp = await app_client.get(f"/users/{user2_id}")
    taken_name = resp.json()["user"]["username"]

    resp = await app_client.patch(
        "/users/me/username", json={"username": taken_name}, headers=_bearer(user1_token)
    )
    assert resp.status_code == 409


async def test_change_username_strips_whitespace(app_client, user1_token):
    me = await app_client.get("/users/me", headers=_bearer(user1_token))
    original = me.json()["user"]["username"]

    new_name = f"trim_{uuid.uuid4().hex[:8]}"
    resp = await app_client.patch(
        "/users/me/username",
        json={"username": f"  {new_name}  "},
        headers=_bearer(user1_token),
    )
    assert resp.status_code == 200
    assert resp.json()["username"] == new_name

    # Restore
    await app_client.patch(
        "/users/me/username", json={"username": original}, headers=_bearer(user1_token)
    )


# ---------------------------------------------------------------------------
# PATCH /users/{user_id}/username
# ---------------------------------------------------------------------------

async def test_change_user_username_regular_forbidden(app_client, user1_token, user2_id):
    resp = await app_client.patch(
        f"/users/{user2_id}/username",
        json={"username": "hijacked"},
        headers=_bearer(user1_token),
    )
    assert resp.status_code == 403


async def test_change_user_username_admin_forbidden(app_client, admin_token, user1_id):
    resp = await app_client.patch(
        f"/users/{user1_id}/username",
        json={"username": "hijacked"},
        headers=_bearer(admin_token),
    )
    assert resp.status_code == 403


async def test_change_user_username_unknown_user(app_client, super_admin_token):
    resp = await app_client.patch(
        f"/users/{uuid.uuid4()}/username",
        json={"username": "ghost"},
        headers=_bearer(super_admin_token),
    )
    assert resp.status_code == 404


async def test_change_user_username_taken(app_client, super_admin_token, user1_id, user2_id):
    # Get user1's username
    resp = await app_client.get(f"/users/{user1_id}")
    taken_name = resp.json()["user"]["username"]

    resp = await app_client.patch(
        f"/users/{user2_id}/username",
        json={"username": taken_name},
        headers=_bearer(super_admin_token),
    )
    assert resp.status_code == 409


async def test_change_user_username_success(app_client, super_admin_token, user2_id):
    # Get current username to restore
    resp = await app_client.get(f"/users/{user2_id}")
    original = resp.json()["user"]["username"]

    new_name = f"admin_renamed_{uuid.uuid4().hex[:8]}"
    resp = await app_client.patch(
        f"/users/{user2_id}/username",
        json={"username": new_name},
        headers=_bearer(super_admin_token),
    )
    assert resp.status_code == 200
    assert resp.json()["username"] == new_name

    # Restore
    await app_client.patch(
        f"/users/{user2_id}/username",
        json={"username": original},
        headers=_bearer(super_admin_token),
    )


# ---------------------------------------------------------------------------
# PATCH /users/me/email
# ---------------------------------------------------------------------------

async def test_change_email_no_auth(app_client):
    resp = await app_client.patch("/users/me/email", json={"email": "new@test.com"})
    assert resp.status_code == 422


async def test_change_email_bad_token(app_client):
    resp = await app_client.patch(
        "/users/me/email", json={"email": "new@test.com"}, headers=_bearer("garbage")
    )
    assert resp.status_code == 401


async def test_change_email_invalid(app_client, user1_token):
    resp = await app_client.patch(
        "/users/me/email", json={"email": "not-an-email"}, headers=_bearer(user1_token)
    )
    assert resp.status_code == 422


async def test_change_email_success(app_client, sync_supabase, user1_token):
    if sync_supabase is None:
        pytest.skip("no test.env")
    # Use a sacrificial user so we don't break user1's session
    uid, token, _ = _create_sacrificial_user(sync_supabase, "email_change")
    try:
        new_email = f"changed_{uuid.uuid4().hex[:8]}@test.com"
        resp = await app_client.patch(
            "/users/me/email", json={"email": new_email}, headers=_bearer(token)
        )
        assert resp.status_code == 200
        assert "Confirmation email sent" in resp.json()["message"]
    finally:
        sync_supabase.auth.admin.delete_user(uid)


# ---------------------------------------------------------------------------
# PATCH /users/{user_id}/email
# ---------------------------------------------------------------------------

async def test_change_user_email_regular_forbidden(app_client, user1_token, user2_id):
    resp = await app_client.patch(
        f"/users/{user2_id}/email",
        json={"email": "hijack@test.com"},
        headers=_bearer(user1_token),
    )
    assert resp.status_code == 403


async def test_change_user_email_unknown_user(app_client, super_admin_token):
    resp = await app_client.patch(
        f"/users/{uuid.uuid4()}/email",
        json={"email": "ghost@test.com"},
        headers=_bearer(super_admin_token),
    )
    assert resp.status_code == 404


async def test_change_user_email_invalid(app_client, super_admin_token, user1_id):
    resp = await app_client.patch(
        f"/users/{user1_id}/email",
        json={"email": "not-valid"},
        headers=_bearer(super_admin_token),
    )
    assert resp.status_code == 422


async def test_change_user_email_success(app_client, sync_supabase, super_admin_token):
    if sync_supabase is None:
        pytest.skip("no test.env")
    uid, _, _ = _create_sacrificial_user(sync_supabase, "admin_email")
    try:
        new_email = f"admin_changed_{uuid.uuid4().hex[:8]}@test.com"
        resp = await app_client.patch(
            f"/users/{uid}/email",
            json={"email": new_email},
            headers=_bearer(super_admin_token),
        )
        assert resp.status_code == 200
        assert resp.json()["email"] == new_email
    finally:
        sync_supabase.auth.admin.delete_user(uid)


# ---------------------------------------------------------------------------
# DELETE /users/me
# ---------------------------------------------------------------------------

async def test_delete_me_no_auth(app_client):
    resp = await app_client.delete("/users/me")
    assert resp.status_code == 422


async def test_delete_me_bad_token(app_client):
    resp = await app_client.delete("/users/me", headers=_bearer("garbage"))
    assert resp.status_code == 401


async def test_delete_me_success(app_client, sync_supabase):
    if sync_supabase is None:
        pytest.skip("no test.env")
    uid, token, _ = _create_sacrificial_user(sync_supabase, "delete_self")
    resp = await app_client.delete("/users/me", headers=_bearer(token))
    assert resp.status_code == 200
    assert resp.json()["deleted"] == uid

    # Verify user is gone
    profile_resp = await app_client.get(f"/users/{uid}")
    assert profile_resp.status_code == 404


async def test_delete_me_match_history_preserved(app_client, sync_supabase, super_admin_token):
    """After deleting a user, their matches should reference the sentinel."""
    if sync_supabase is None:
        pytest.skip("no test.env")
    uid_a, token_a, name_a = _create_sacrificial_user(sync_supabase, "del_hist_a")
    uid_b, token_b, name_b = _create_sacrificial_user(sync_supabase, "del_hist_b")

    try:
        # Report a match: A beats B (use superAdmin to report for any two)
        opts = await app_client.get("/options")
        rule_set_id = opts.json()["rule_sets"][0]["id"]
        match_resp = await app_client.post(
            "/matches",
            json={"winner_id": uid_a, "loser_id": uid_b, "rule_set_id": rule_set_id},
            headers=_bearer(super_admin_token),
        )
        assert match_resp.status_code == 201
        match_id = match_resp.json()["match"]["id"]

        # Confirm the match
        confirm_resp = await app_client.post(
            "/matches/confirm",
            json={"match_ids": [match_id]},
            headers=_bearer(super_admin_token),
        )
        assert confirm_resp.status_code == 200

        # Record original winner name
        original_winner_name = match_resp.json()["match"]["winnerName"]

        # Delete user A via self-deletion
        del_resp = await app_client.delete("/users/me", headers=_bearer(token_a))
        assert del_resp.status_code == 200

        # Fetch the match — winnerId should now be sentinel, winnerName unchanged
        match_detail = await app_client.get(f"/matches/{match_id}")
        assert match_detail.status_code == 200
        match_data = match_detail.json()["match"]
        assert match_data["winnerId"] == DELETED_USER_SENTINEL_ID
        assert match_data["winnerName"] == original_winner_name
    finally:
        # Clean up user B (user A already deleted)
        sync_supabase.auth.admin.delete_user(uid_b)


# ---------------------------------------------------------------------------
# DELETE /users/{user_id}
# ---------------------------------------------------------------------------

async def test_delete_user_regular_forbidden(app_client, sync_supabase, user1_token):
    if sync_supabase is None:
        pytest.skip("no test.env")
    uid, _, _ = _create_sacrificial_user(sync_supabase, "del_forbidden")
    try:
        resp = await app_client.delete(f"/users/{uid}", headers=_bearer(user1_token))
        assert resp.status_code == 403
    finally:
        sync_supabase.auth.admin.delete_user(uid)


async def test_delete_user_admin_forbidden(app_client, sync_supabase, admin_token):
    if sync_supabase is None:
        pytest.skip("no test.env")
    uid, _, _ = _create_sacrificial_user(sync_supabase, "del_admin_forbid")
    try:
        resp = await app_client.delete(f"/users/{uid}", headers=_bearer(admin_token))
        assert resp.status_code == 403
    finally:
        sync_supabase.auth.admin.delete_user(uid)


async def test_delete_user_unknown(app_client, super_admin_token):
    resp = await app_client.delete(
        f"/users/{uuid.uuid4()}", headers=_bearer(super_admin_token)
    )
    assert resp.status_code == 404


async def test_delete_user_sentinel_blocked(app_client, super_admin_token):
    resp = await app_client.delete(
        f"/users/{DELETED_USER_SENTINEL_ID}", headers=_bearer(super_admin_token)
    )
    assert resp.status_code == 400


async def test_delete_user_bootstrap_blocked(app_client, super_admin_token, super_admin_id):
    resp = await app_client.delete(
        f"/users/{super_admin_id}", headers=_bearer(super_admin_token)
    )
    assert resp.status_code == 400


async def test_delete_user_success(app_client, sync_supabase, super_admin_token):
    if sync_supabase is None:
        pytest.skip("no test.env")
    uid, _, _ = _create_sacrificial_user(sync_supabase, "del_by_admin")
    resp = await app_client.delete(f"/users/{uid}", headers=_bearer(super_admin_token))
    assert resp.status_code == 200
    assert resp.json()["deleted"] == uid


async def test_delete_user_verify_gone(app_client, sync_supabase, super_admin_token):
    if sync_supabase is None:
        pytest.skip("no test.env")
    uid, _, _ = _create_sacrificial_user(sync_supabase, "del_verify_gone")
    await app_client.delete(f"/users/{uid}", headers=_bearer(super_admin_token))

    profile_resp = await app_client.get(f"/users/{uid}")
    assert profile_resp.status_code == 404
