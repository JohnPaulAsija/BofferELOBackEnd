"""
Unit tests for helpers.resolve_token.

Uses unittest.mock to avoid any real network calls.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import HTTPException
from supabase import AuthApiError

from helpers import resolve_token, resolve_user_profile


def _make_supabase(*, side_effect=None, return_value=None):
    """Build a minimal mock AsyncClient whose auth.get_user is controllable."""
    supabase = MagicMock()
    if side_effect is not None:
        supabase.auth.get_user = AsyncMock(side_effect=side_effect)
    else:
        supabase.auth.get_user = AsyncMock(return_value=return_value)
    return supabase


# ---------------------------------------------------------------------------
# Header format validation (no network call reached)
# ---------------------------------------------------------------------------

async def test_missing_bearer_prefix_raises_401():
    supabase = _make_supabase(return_value=MagicMock())
    with pytest.raises(HTTPException) as exc_info:
        await resolve_token("some-token-without-prefix", supabase)
    assert exc_info.value.status_code == 401


async def test_bearer_without_space_raises_401():
    supabase = _make_supabase(return_value=MagicMock())
    with pytest.raises(HTTPException) as exc_info:
        await resolve_token("Bearertoken", supabase)
    assert exc_info.value.status_code == 401


async def test_empty_string_raises_401():
    supabase = _make_supabase(return_value=MagicMock())
    with pytest.raises(HTTPException) as exc_info:
        await resolve_token("", supabase)
    assert exc_info.value.status_code == 401


async def test_just_bearer_keyword_raises_401():
    """'Bearer ' (with trailing space but no token) should still fail
    because the extracted token is empty."""
    # get_user will be called with an empty string — simulate an AuthApiError
    supabase = _make_supabase(side_effect=AuthApiError("invalid", 401, {}))
    with pytest.raises(HTTPException) as exc_info:
        await resolve_token("Bearer ", supabase)
    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# Valid header format — test what get_user returns/raises
# ---------------------------------------------------------------------------

async def test_valid_token_returns_user():
    fake_user = MagicMock()
    supabase = _make_supabase(return_value=fake_user)
    result = await resolve_token("Bearer valid.jwt.token", supabase)
    assert result is fake_user
    supabase.auth.get_user.assert_awaited_once_with("valid.jwt.token")


async def test_auth_api_error_raises_401():
    supabase = _make_supabase(side_effect=AuthApiError("expired", 401, {}))
    with pytest.raises(HTTPException) as exc_info:
        await resolve_token("Bearer expired.token", supabase)
    assert exc_info.value.status_code == 401


async def test_unexpected_exception_raises_401():
    supabase = _make_supabase(side_effect=RuntimeError("network blip"))
    with pytest.raises(HTTPException) as exc_info:
        await resolve_token("Bearer some.token", supabase)
    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# resolve_user_profile — malformed profile (NULL role_id)
# ---------------------------------------------------------------------------

def _make_supabase_with_profile(*, user_id="user-123", role_id, username=None):
    """Build a mock AsyncClient that returns a valid token and a profile row."""
    fake_user = MagicMock()
    fake_user.user.id = user_id

    profile_result = MagicMock()
    profile_result.data = {"role_id": role_id, "username": username}

    query_chain = MagicMock()
    query_chain.execute = AsyncMock(return_value=profile_result)
    query_chain.single.return_value = query_chain
    query_chain.eq.return_value = query_chain
    query_chain.select.return_value = query_chain

    supabase = MagicMock()
    supabase.auth.get_user = AsyncMock(return_value=fake_user)
    supabase.from_.return_value = query_chain
    return supabase


async def test_null_username_raises_403():
    """Profile row exists but username is NULL (setup not complete) → 403."""
    supabase = _make_supabase_with_profile(role_id=1, username=None)
    with pytest.raises(HTTPException) as exc_info:
        await resolve_user_profile("Bearer valid.jwt", supabase)
    assert exc_info.value.status_code == 403
    assert "setup" in exc_info.value.detail.lower()


async def test_valid_profile_returns_dict():
    supabase = _make_supabase_with_profile(role_id=1, username="fighter1")
    result = await resolve_user_profile("Bearer valid.jwt", supabase)
    assert result == {"user_id": "user-123", "role_id": 1, "username": "fighter1"}


async def test_missing_profile_raises_404():
    fake_user = MagicMock()
    fake_user.user.id = "user-123"

    profile_result = MagicMock()
    profile_result.data = None

    query_chain = MagicMock()
    query_chain.execute = AsyncMock(return_value=profile_result)
    query_chain.single.return_value = query_chain
    query_chain.eq.return_value = query_chain
    query_chain.select.return_value = query_chain

    supabase = MagicMock()
    supabase.auth.get_user = AsyncMock(return_value=fake_user)
    supabase.from_.return_value = query_chain

    with pytest.raises(HTTPException) as exc_info:
        await resolve_user_profile("Bearer valid.jwt", supabase)
    assert exc_info.value.status_code == 404
