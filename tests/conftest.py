"""
Shared pytest fixtures.

Integration tests opt in via RUN_INTEGRATION_TESTS=1; without it,
pytest_collection_modifyitems below skips every test file other than the
unit-test files. The reset_and_seed fixture creates per-run UUID-namespaced
test users and matches against whatever Supabase project API_URL points
at, then surgically deletes only the entities it created in teardown.

Never calls POST /admin/reset. Never touches data it did not create.
"""
import os
import base64
import json
import uuid

import httpx
import pytest
import pytest_asyncio
from dotenv import load_dotenv

# Single env source: .env. RUN_INTEGRATION_TESTS=1 inside it (or in the
# shell environment) is what unlocks the integration suite.
load_dotenv()

_UNIT_TEST_FILES = {"test_helpers.py", "test_rate_limit.py"}
_INTEGRATION_SKIP_REASON = "integration tests opt-in via RUN_INTEGRATION_TESTS=1"


def pytest_collection_modifyitems(config, items):
    """Skip every integration test at collection time unless explicitly opted in.

    Unit-test files are identified by name; everything else is treated as
    integration. Keeps the default `pytest` run cheap and prod-safe.
    """
    if os.environ.get("RUN_INTEGRATION_TESTS") == "1":
        return
    skip_marker = pytest.mark.skip(reason=_INTEGRATION_SKIP_REASON)
    for item in items:
        if os.path.basename(str(item.fspath)) not in _UNIT_TEST_FILES:
            item.add_marker(skip_marker)


def pytest_sessionfinish(session, exitstatus):
    """Warn if any test users namespaced to this run survived teardown.

    Best-effort sanity check — the per-run UUID prefix keeps collisions
    with prod users effectively impossible, but this catches fixture bugs
    that drop entries from the cleanup registry.
    """
    if os.environ.get("RUN_INTEGRATION_TESTS") != "1":
        return
    run_id = getattr(session, "_bofferelo_test_run_id", None)
    if not run_id:
        return
    try:
        from initialize import create_client
        client = create_client()
        users = client.auth.admin.list_users()
        leaked = [u.email for u in users if (u.email or "").startswith(f"test_{run_id}_")]
        if leaked:
            print(f"\nWARNING: test run {run_id} leaked {len(leaked)} user(s): {leaked}")
    except Exception as e:
        print(f"\nWARNING: post-run leak check failed: {e}")


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

    Yields None when RUN_INTEGRATION_TESTS is not "1" so that unit tests
    don't trigger the FastAPI lifespan (which would otherwise crash with
    KeyError: 'API_URL' when no .env is loaded). The collection hook
    above generally prevents integration tests from being collected when
    the gate is off, but this is belt-and-suspenders.
    """
    if os.environ.get("RUN_INTEGRATION_TESTS") != "1":
        yield None
        return
    from api import app, lifespan
    async with lifespan(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client


@pytest_asyncio.fixture(scope="session", autouse=True)
async def reset_and_seed(app_client, request):
    """
    Create a per-run set of test users + matches against the live Supabase
    project, surgically delete them on session teardown. No POST /admin/reset
    call, no fixed emails, no data outside this run's namespace is touched.

    The per-run namespace is `run_id = uuid.uuid4().hex[:8]`; emails are
    `test_<run_id>_<role>@bofferelo-test.invalid` (RFC 2606 reserved TLD).

    Short-circuits when app_client is None (gate off → integration tests
    not opted in).
    """
    if app_client is None:
        yield {}
        return

    from initialize import create_client
    from helpers import DELETED_USER_SENTINEL_ID

    sync_client = create_client()
    run_id = uuid.uuid4().hex[:8]
    request.session._bofferelo_test_run_id = run_id  # surfaced to pytest_sessionfinish
    test_password = os.environ.get("TEST_PASSWORD", "TestPassword123!")

    # Registry of everything this run creates — only these IDs are deleted on teardown.
    created = {"auth_user_ids": [], "match_ids": []}

    # name → role_id. role_id 1 = user, 2 = admin, 3 = superAdmin.
    accounts = [
        ("user1", 1),
        ("user2", 1),
        ("user3", 1),
        ("admin", 2),
        ("super_admin", 3),
    ]
    result = {}

    try:
        for name, role_id in accounts:
            email = f"test_{run_id}_{name}@bofferelo-test.invalid"
            username = f"test_{run_id}_{name}"
            user_resp = sync_client.auth.admin.create_user({
                "email": email,
                "password": test_password,
                "email_confirm": True,
                "user_metadata": {"username": username},
            })
            uid = user_resp.user.id
            created["auth_user_ids"].append(uid)

            # The on_auth_user_created trigger seeds username, termsAcceptedAt,
            # elo=1000, wins=0, losses=0, role_id=1. Only need to upgrade role
            # for admin/superAdmin.
            if role_id != 1:
                sync_client.from_("profiles").update({"role_id": role_id}).eq("id", uid).execute()

            tmp = create_client()
            sign_in = tmp.auth.sign_in_with_password(
                {"email": email, "password": test_password}
            )
            result[f"{name}_token"] = sign_in.session.access_token
            result[f"{name}_id"] = uid

        # --- Seed matches (3 confirmed, 2 unconfirmed) ---
        opts_resp = await app_client.get("/options")
        rule_set_id = opts_resp.json()["rule_sets"][0]["id"]

        user1_id = result["user1_id"]
        user2_id = result["user2_id"]
        user3_id = result["user3_id"]
        user1_tok = result["user1_token"]
        user2_tok = result["user2_token"]
        user3_tok = result["user3_token"]

        to_confirm = []
        for _ in range(3):
            r = await app_client.post(
                "/matches",
                json={"winner_id": user1_id, "loser_id": user2_id, "rule_set_id": rule_set_id},
                headers={"Authorization": f"Bearer {user1_tok}"},
            )
            if r.status_code == 201:
                mid = r.json()["match"]["id"]
                to_confirm.append(mid)
                created["match_ids"].append(mid)

        if to_confirm:
            await app_client.post(
                "/matches/confirm",
                json={"match_ids": to_confirm},
                headers={"Authorization": f"Bearer {user2_tok}"},
            )

        for _ in range(2):
            r = await app_client.post(
                "/matches",
                json={"winner_id": user1_id, "loser_id": user3_id, "rule_set_id": rule_set_id},
                headers={"Authorization": f"Bearer {user3_tok}"},
            )
            if r.status_code == 201:
                created["match_ids"].append(r.json()["match"]["id"])

        yield result
    finally:
        # Teardown: delete only what this run created. Matches first (FK refs profiles).
        for mid in created["match_ids"]:
            try:
                sync_client.from_("Matches").delete().eq("id", mid).execute()
            except Exception:
                pass  # best-effort
        for uid in created["auth_user_ids"]:
            if uid == DELETED_USER_SENTINEL_ID:
                continue  # never delete the sentinel, even by accident
            try:
                sync_client.auth.admin.delete_user(uid)
            except Exception:
                pass  # best-effort; profile cascades via before_profile_delete trigger


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
    """Expose a service-role Supabase sync client for direct DB manipulation in tests.

    Returns None when integration tests aren't opted in; the collection hook
    above generally prevents tests using this from running in that case.
    """
    if os.environ.get("RUN_INTEGRATION_TESTS") != "1":
        return None
    from initialize import create_client
    return create_client()
