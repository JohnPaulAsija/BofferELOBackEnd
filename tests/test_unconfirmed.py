"""
Integration tests: unconfirmed (pending email) users must not be visible
via public profile or match history endpoints.

Each test creates a sacrificial user with email_confirm=False,
attempts to access their public profile or match history, and asserts 404.
Cleanup happens in finally blocks.
"""
import pytest


@pytest.mark.asyncio
async def test_unconfirmed_user_profile_returns_404(app_client, sync_supabase):
    """Public profile endpoint must return 404 for unconfirmed users."""
    if sync_supabase is None:
        pytest.skip("no test.env — integration tests only")
    user = sync_supabase.auth.admin.create_user({
        "email": "pending_profile@test.com",
        "password": "TestPassword123!",
        "email_confirm": False,
        "user_metadata": {"username": "pending_profile"},
    })
    pending_id = str(user.user.id)
    try:
        resp = await app_client.get(f"/users/{pending_id}")
        assert resp.status_code == 404, "Unconfirmed user's profile must not be publicly accessible"
    finally:
        sync_supabase.auth.admin.delete_user(pending_id)


@pytest.mark.asyncio
async def test_unconfirmed_user_match_history_returns_404(app_client, sync_supabase):
    """Public match history endpoint must return 404 for unconfirmed users."""
    if sync_supabase is None:
        pytest.skip("no test.env — integration tests only")
    user = sync_supabase.auth.admin.create_user({
        "email": "pending_history@test.com",
        "password": "TestPassword123!",
        "email_confirm": False,
        "user_metadata": {"username": "pending_history"},
    })
    pending_id = str(user.user.id)
    try:
        resp = await app_client.get(f"/users/{pending_id}/matches")
        assert resp.status_code == 404, "Unconfirmed user's match history must not be publicly accessible"
    finally:
        sync_supabase.auth.admin.delete_user(pending_id)
