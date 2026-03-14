"""
Integration tests for public (unauthenticated) endpoints.

Requires a running Supabase instance reachable via test.env / .env.
The app_client fixture starts the full ASGI app in-process, including
the real lifespan (Supabase async client + FastAPICache).
"""
import uuid

import pytest
from datetime import datetime


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

async def test_root(app_client):
    response = await app_client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "message" in data
    assert isinstance(data["message"], str)


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

async def test_health_returns_ok(app_client):
    response = await app_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["db"] == "ok"


# ---------------------------------------------------------------------------
# GET /users/top  (leaderboard)
# ---------------------------------------------------------------------------

async def test_leaderboard_returns_200(app_client):
    response = await app_client.get("/users/top")
    assert response.status_code == 200


async def test_leaderboard_has_leaderboard_key(app_client):
    response = await app_client.get("/users/top")
    data = response.json()
    assert "leaderboard" in data
    assert isinstance(data["leaderboard"], list)


async def test_leaderboard_entry_shape(app_client):
    response = await app_client.get("/users/top")
    entries = response.json()["leaderboard"]
    if not entries:
        pytest.skip("leaderboard is empty — seed data first")
    for entry in entries:
        assert "id" in entry
        assert "username" in entry
        assert "elo" in entry
        assert "wins" in entry
        assert "losses" in entry


async def test_leaderboard_is_sorted_by_elo_desc(app_client):
    response = await app_client.get("/users/top")
    entries = response.json()["leaderboard"]
    if len(entries) < 2:
        pytest.skip("need at least 2 entries to test sort order")
    elos = [e["elo"] for e in entries]
    assert elos == sorted(elos, reverse=True)


async def test_leaderboard_excludes_incomplete_profiles(app_client, sync_supabase):
    """Users who signed up but never completed setup (NULL username) must not appear."""
    if sync_supabase is None:
        pytest.skip("no test.env — integration tests only")
    user = sync_supabase.auth.admin.create_user(
        {"email": "incomplete_lb@test.com", "password": "TestPassword123!", "email_confirm": True}
    )
    incomplete_id = user.user.id
    try:
        response = await app_client.get("/users/top")
        assert response.status_code == 200
        ids = [e["id"] for e in response.json()["leaderboard"]]
        assert incomplete_id not in ids
    finally:
        sync_supabase.auth.admin.delete_user(incomplete_id)


# ---------------------------------------------------------------------------
# GET /matches  (recent confirmed matches)
# ---------------------------------------------------------------------------

async def test_recent_matches_returns_200(app_client):
    response = await app_client.get("/matches")
    assert response.status_code == 200


async def test_recent_matches_has_matches_key(app_client):
    response = await app_client.get("/matches")
    data = response.json()
    assert "matches" in data
    assert isinstance(data["matches"], list)


async def test_recent_matches_entry_shape(app_client):
    response = await app_client.get("/matches")
    matches = response.json()["matches"]
    if not matches:
        pytest.skip("no confirmed matches — seed data first")
    for m in matches:
        assert "id" in m
        assert "winnerId" in m
        assert "winnerName" in m
        assert "loserId" in m
        assert "loserName" in m
        assert "eloChange" in m
        assert "confirmedAt" in m
        assert "ruleSetId" in m


async def test_recent_matches_are_sorted_by_confirmed_at_desc(app_client):
    response = await app_client.get("/matches")
    matches = response.json()["matches"]
    if len(matches) < 2:
        pytest.skip("need at least 2 confirmed matches to test sort order")
    timestamps = [datetime.fromisoformat(m["confirmedAt"].replace("Z", "+00:00")) for m in matches]
    assert timestamps == sorted(timestamps, reverse=True)


async def test_recent_matches_all_have_confirmed_at(app_client):
    """Unconfirmed matches must never appear in the public feed."""
    response = await app_client.get("/matches")
    matches = response.json()["matches"]
    for m in matches:
        assert m["confirmedAt"] is not None


# ---------------------------------------------------------------------------
# GET /users/{user_id}  (public profile)
# ---------------------------------------------------------------------------

async def test_user_profile_unknown(app_client):
    resp = await app_client.get(f"/users/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_user_profile_invalid_uuid(app_client):
    resp = await app_client.get("/users/not-a-uuid")
    assert resp.status_code == 422


async def test_user_profile_returns_200(app_client, user1_id):
    resp = await app_client.get(f"/users/{user1_id}")
    assert resp.status_code == 200


async def test_user_profile_shape(app_client, user1_id):
    resp = await app_client.get(f"/users/{user1_id}")
    user = resp.json()["user"]
    assert "id" in user
    assert "username" in user
    assert "elo" in user
    assert "wins" in user
    assert "losses" in user


# ---------------------------------------------------------------------------
# GET /users/{user_id}/matches  (public match history)
# ---------------------------------------------------------------------------

async def test_user_matches_unknown(app_client):
    resp = await app_client.get(f"/users/{uuid.uuid4()}/matches")
    assert resp.status_code == 404


async def test_user_matches_invalid_uuid(app_client):
    resp = await app_client.get("/users/not-a-uuid/matches")
    assert resp.status_code == 422


async def test_user_matches_returns_200(app_client, user1_id):
    resp = await app_client.get(f"/users/{user1_id}/matches")
    assert resp.status_code == 200


async def test_user_matches_has_keys(app_client, user1_id):
    resp = await app_client.get(f"/users/{user1_id}/matches")
    data = resp.json()
    assert "matches" in data
    assert isinstance(data["matches"], list)
    assert "next_cursor" in data


async def test_user_matches_only_confirmed(app_client, user1_id):
    resp = await app_client.get(f"/users/{user1_id}/matches")
    matches = resp.json()["matches"]
    if not matches:
        pytest.skip("no match history for this user")
    for m in matches:
        assert m["confirmedAt"] is not None


async def test_user_matches_pagination(app_client, user1_id):
    resp = await app_client.get(f"/users/{user1_id}/matches", params={"limit": 1})
    data = resp.json()
    if len(data["matches"]) == 0:
        pytest.skip("no matches to paginate")
    if data["next_cursor"] is None:
        pytest.skip("only 1 match — cannot test pagination")
    # Fetch the second page
    resp2 = await app_client.get(
        f"/users/{user1_id}/matches",
        params={"limit": 1, "before": data["next_cursor"]},
    )
    page2 = resp2.json()
    assert resp2.status_code == 200
    assert isinstance(page2["matches"], list)
    # Pages should return different matches
    if page2["matches"]:
        assert page2["matches"][0]["id"] != data["matches"][0]["id"]


# ---------------------------------------------------------------------------
# GET /options  (rulesets)
# ---------------------------------------------------------------------------

async def test_options_has_rule_sets(app_client):
    resp = await app_client.get("/options")
    assert resp.status_code == 200
    data = resp.json()
    assert "rule_sets" in data
    assert isinstance(data["rule_sets"], list)
    names = [rs["name"] for rs in data["rule_sets"]]
    assert "Dagorhir" in names
    assert "Hearthlight" in names
    for rs in data["rule_sets"]:
        assert "id" in rs
        assert "name" in rs


# ---------------------------------------------------------------------------
# POST /matches  (rule_set_id validation)
# ---------------------------------------------------------------------------

async def test_report_match_missing_rule_set_id(app_client, user1_token, user1_id, user2_id):
    resp = await app_client.post(
        "/matches",
        json={"winner_id": user1_id, "loser_id": user2_id},
        headers={"Authorization": f"Bearer {user1_token}"},
    )
    assert resp.status_code == 422


async def test_report_match_invalid_rule_set_id(app_client, user1_token, user1_id, user2_id):
    resp = await app_client.post(
        "/matches",
        json={"winner_id": user1_id, "loser_id": user2_id, "rule_set_id": "not-a-uuid"},
        headers={"Authorization": f"Bearer {user1_token}"},
    )
    assert resp.status_code == 422


async def test_report_match_with_rule_set_id(app_client, user1_token, user1_id, user2_id):
    # Fetch a valid ruleset ID from /options
    opts = await app_client.get("/options")
    rule_set_id = opts.json()["rule_sets"][0]["id"]

    resp = await app_client.post(
        "/matches",
        json={"winner_id": user1_id, "loser_id": user2_id, "rule_set_id": rule_set_id},
        headers={"Authorization": f"Bearer {user1_token}"},
    )
    assert resp.status_code == 201
    match = resp.json()["match"]
    assert match["ruleSetId"] == rule_set_id
