"""
Shared pytest fixtures.

Phase 1: `app_client` (ASGI transport).
Phase 3: `reset_and_seed` (session-scoped DB reset + account creation),
         token/ID fixtures, and helper functions.
"""
import os
import base64
import json

import httpx
import pytest_asyncio
from dotenv import load_dotenv

# Load test.env so integration tests can reach the real Supabase instance.
# Falls back silently if the file doesn't exist (unit tests don't need it).
load_dotenv("test.env")


# ---------------------------------------------------------------------------
# Helper functions (not fixtures) — importable by test modules
# ---------------------------------------------------------------------------

def _bearer(token: str) -> dict:
    """Return an Authorization header dict for use with app_client."""
    return {"Authorization": f"Bearer {token}"}


def _decode_jwt_sub(token: str) -> str:
    """Decode JWT payload (no sig verification) and return the sub claim."""
    payload_b64 = token.split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)
    payload = json.loads(base64.b64decode(payload_b64))
    return payload["sub"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="session")
async def app_client():
    """
    An httpx.AsyncClient that talks to the FastAPI app in-process via ASGI.

    Session-scoped so the lifespan (Supabase client + FastAPICache) starts
    once and is reused across all tests.
    """
    from api import app, lifespan
    async with lifespan(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client


@pytest_asyncio.fixture(scope="session", autouse=True)
async def reset_and_seed(app_client):
    """
    Reset the test DB and create fresh test accounts once per session.

    1. Sign in as bootstrap superAdmin
    2. POST /admin/reset  (delete all matches + non-bootstrap auth users)
    3. Ensure superAdmin profile has valid fields
    4. Create 4 test accounts (3 users + 1 admin)
    5. Update role_id for elevated accounts (trigger sets all other fields)
    6. Sign in all 5 accounts and return tokens + IDs

    Skips gracefully if test.env vars are missing (unit-test-only runs).
    """
    sa_email = os.environ.get("SUPER_ADMIN_EMAIL")
    if not sa_email:
        return {}

    from initialize import create_client

    sync_client = create_client()

    sa_email = os.environ["SUPER_ADMIN_EMAIL"]
    sa_password = os.environ["SUPER_ADMIN_PASSWORD"]
    test_password = os.environ.get("TEST_PASSWORD", "TestPassword123!")

    # Sign in superAdmin via a throwaway client to get the JWT
    # without tainting the service-role client's auth state.
    sign_in_client = create_client()
    sa_auth = sign_in_client.auth.sign_in_with_password(
        {"email": sa_email, "password": sa_password}
    )
    sa_token = sa_auth.session.access_token
    sa_id = sa_auth.user.id

    resp = await app_client.post(
        "/admin/reset", headers={"Authorization": f"Bearer {sa_token}"}
    )
    assert resp.status_code == 200, f"reset failed: {resp.text}"

    # Ensure superAdmin profile has valid numeric fields
    sync_client.from_("profiles").update(
        {"elo": 1000, "wins": 0, "losses": 0}
    ).eq("id", sa_id).is_("wins", "null").execute()

    # Create 4 test accounts (sync_client still has service-role auth)
    accounts = [
        ("user1", os.environ["TEST_USER1_EMAIL"], 1),
        ("user2", os.environ["TEST_USER2_EMAIL"], 1),
        ("user3", os.environ["TEST_USER3_EMAIL"], 1),
        ("admin", os.environ["TEST_ADMIN_EMAIL"], 2),
    ]
    result = {"super_admin_token": sa_token, "super_admin_id": sa_id}

    for name, email, role_id in accounts:
        username = email.split("@")[0]
        user_resp = sync_client.auth.admin.create_user({
            "email": email,
            "password": test_password,
            "email_confirm": True,
            "user_metadata": {"username": username},
        })
        uid = user_resp.user.id

        # Trigger sets username, termsAcceptedAt, elo=1000, wins=0, losses=0.
        # Only update role_id for elevated roles (trigger always defaults to 1).
        if role_id != 1:
            sync_client.from_("profiles").update({"role_id": role_id}).eq("id", uid).execute()

        tmp = create_client()
        sign_in = tmp.auth.sign_in_with_password(
            {"email": email, "password": test_password}
        )
        result[f"{name}_token"] = sign_in.session.access_token
        result[f"{name}_id"] = uid

    return result


# --- Token fixtures ---

@pytest_asyncio.fixture(scope="session")
async def user1_token(reset_and_seed):
    return reset_and_seed["user1_token"]

@pytest_asyncio.fixture(scope="session")
async def user2_token(reset_and_seed):
    return reset_and_seed["user2_token"]

@pytest_asyncio.fixture(scope="session")
async def user3_token(reset_and_seed):
    return reset_and_seed["user3_token"]

@pytest_asyncio.fixture(scope="session")
async def admin_token(reset_and_seed):
    return reset_and_seed["admin_token"]

@pytest_asyncio.fixture(scope="session")
async def super_admin_token(reset_and_seed):
    return reset_and_seed["super_admin_token"]


# --- ID fixtures ---

@pytest_asyncio.fixture(scope="session")
async def user1_id(reset_and_seed):
    return reset_and_seed["user1_id"]

@pytest_asyncio.fixture(scope="session")
async def user2_id(reset_and_seed):
    return reset_and_seed["user2_id"]

@pytest_asyncio.fixture(scope="session")
async def user3_id(reset_and_seed):
    return reset_and_seed["user3_id"]

@pytest_asyncio.fixture(scope="session")
async def admin_id(reset_and_seed):
    return reset_and_seed["admin_id"]

@pytest_asyncio.fixture(scope="session")
async def super_admin_id(reset_and_seed):
    return reset_and_seed["super_admin_id"]


@pytest_asyncio.fixture(scope="session")
async def sync_supabase():
    """Expose a service-role Supabase sync client for direct DB manipulation in tests."""
    sa_email = os.environ.get("SUPER_ADMIN_EMAIL")
    if not sa_email:
        return None
    from initialize import create_client
    return create_client()
