# Block Unconfirmed Users from Acting — Implementation Plan

> **STATUS: DROPPED (2026-04-03)** — Supabase enforces email confirmation at sign-in (returns 400 "Email not confirmed"). Unconfirmed users cannot obtain a JWT, so they cannot hit any authenticated endpoint. Application-level enforcement is unnecessary.

**Goal:** Prevent users who haven't confirmed their email from performing write actions (reporting/confirming/rejecting matches, changing username/email, updating preferences).

**Prerequisite:** The `email_confirmed` column and triggers from plan `2026-03-29-exclude-pending-accounts` must be applied first.

**Architecture:** Add an `email_confirmed` check to `resolve_user_profile` in `helpers.py`. Endpoints that should block unconfirmed users already call this function. Three endpoints that currently use `resolve_token` (lighter, JWT-only) are switched to `resolve_user_profile` to gain the check.

**Tech Stack:** Python 3.11, FastAPI, Supabase, `supabase-py` async client, pytest-asyncio.

---

## Task 1: Write Tests for Blocked Endpoints

Tests use a sacrificial pending user created with `email_confirm: False`, cleaned up in a `finally` block. Each test authenticates as the pending user and asserts 403.

**Files:**
- Create or modify: `tests/test_unconfirmed.py`

**Step 1: Create the test file**

```python
"""
Integration tests: unconfirmed (pending email) users must be blocked from write actions.

Each test creates a sacrificial user with email_confirm=False, authenticates,
attempts the action, and asserts 403. Cleanup happens in a finally block.
"""
import pytest
from tests.conftest import _bearer


@pytest.fixture
def pending_user(sync_supabase):
    """Create a pending (unconfirmed email) user and return (token, user_id). Clean up after."""
    if sync_supabase is None:
        pytest.skip("no test.env — integration tests only")
    from initialize import create_client
    user = sync_supabase.auth.admin.create_user({
        "email": "pending_block@test.com",
        "password": "TestPassword123!",
        "email_confirm": False,
        "user_metadata": {"username": "pending_block"},
    })
    uid = str(user.user.id)
    tmp = create_client()
    sign_in = tmp.auth.sign_in_with_password({
        "email": "pending_block@test.com",
        "password": "TestPassword123!",
    })
    token = sign_in.session.access_token
    yield token, uid
    sync_supabase.auth.admin.delete_user(uid)


@pytest.mark.asyncio
async def test_unconfirmed_cannot_report_match(app_client, pending_user, user1_id):
    token, uid = pending_user
    resp = await app_client.post("/matches", json={
        "winner_id": uid,
        "loser_id": str(user1_id),
        "rule_set_id": "00000000-0000-0000-0000-000000000001",  # will fail before validation
    }, headers=_bearer(token))
    assert resp.status_code == 403
    assert "email" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_unconfirmed_cannot_confirm_match(app_client, pending_user):
    token, _ = pending_user
    resp = await app_client.post("/matches/confirm", json={
        "match_ids": ["00000000-0000-0000-0000-000000000099"],
    }, headers=_bearer(token))
    assert resp.status_code == 403
    assert "email" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_unconfirmed_cannot_reject_match(app_client, pending_user):
    token, _ = pending_user
    resp = await app_client.post("/matches/reject", json={
        "match_ids": ["00000000-0000-0000-0000-000000000099"],
    }, headers=_bearer(token))
    assert resp.status_code == 403
    assert "email" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_unconfirmed_cannot_update_preferences(app_client, pending_user):
    token, _ = pending_user
    resp = await app_client.patch("/users/me/preferences", json={
        "gender": "Male",
    }, headers=_bearer(token))
    assert resp.status_code == 403
    assert "email" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_unconfirmed_cannot_change_username(app_client, pending_user):
    token, _ = pending_user
    resp = await app_client.patch("/users/me/username", json={
        "username": "newname123",
    }, headers=_bearer(token))
    assert resp.status_code == 403
    assert "email" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_unconfirmed_cannot_change_email(app_client, pending_user):
    token, _ = pending_user
    resp = await app_client.patch("/users/me/email", json={
        "email": "newemail@test.com",
    }, headers=_bearer(token))
    assert resp.status_code == 403
    assert "email" in resp.json()["detail"].lower()
```

**Step 2: Run the tests — expect all 6 to FAIL**

```
uv run pytest tests/test_unconfirmed.py -v
```

Expected: 6 failures (403 not returned yet).

---

## Task 2: Write Tests for Allowed Endpoints

Unconfirmed users should still be able to view their own profile and delete their account.

**Files:**
- Modify: `tests/test_unconfirmed.py`

**Step 1: Add allowed-action tests to the same file**

```python
@pytest.mark.asyncio
async def test_unconfirmed_can_view_own_profile(app_client, pending_user):
    token, uid = pending_user
    resp = await app_client.get("/users/me", headers=_bearer(token))
    assert resp.status_code == 200
    assert resp.json()["user"]["id"] == uid


@pytest.mark.asyncio
async def test_unconfirmed_can_list_users(app_client, pending_user):
    token, _ = pending_user
    resp = await app_client.get("/users", headers=_bearer(token))
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_unconfirmed_can_view_own_matches(app_client, pending_user):
    token, _ = pending_user
    resp = await app_client.get("/users/me/matches", headers=_bearer(token))
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_unconfirmed_can_delete_own_account(app_client, sync_supabase):
    """Uses a separate sacrificial user so the pending_user fixture isn't broken."""
    if sync_supabase is None:
        pytest.skip("no test.env — integration tests only")
    from initialize import create_client
    user = sync_supabase.auth.admin.create_user({
        "email": "pending_delete@test.com",
        "password": "TestPassword123!",
        "email_confirm": False,
        "user_metadata": {"username": "pending_del"},
    })
    uid = str(user.user.id)
    try:
        tmp = create_client()
        sign_in = tmp.auth.sign_in_with_password({
            "email": "pending_delete@test.com",
            "password": "TestPassword123!",
        })
        resp = await app_client.delete("/users/me", headers=_bearer(sign_in.session.access_token))
        assert resp.status_code == 200
        assert resp.json()["deleted"] == uid
    finally:
        # Account may already be deleted; ignore errors
        try:
            sync_supabase.auth.admin.delete_user(uid)
        except Exception:
            pass
```

**Step 2: Run these — expect all 4 to PASS (already allowed)**

```
uv run pytest tests/test_unconfirmed.py -k "can_" -v
```

---

## Task 3: Add `email_confirmed` Check to `resolve_user_profile`

**Files:**
- Modify: `helpers.py`

**Step 1: Update the profile SELECT and add the check**

In `resolve_user_profile`, change the SELECT to include `email_confirmed` and add a check after the username check:

```python
async def resolve_user_profile(authorization: str, supabase: AsyncClient) -> dict:
    """
    Resolve JWT and fetch the user's profile.

    Returns dict with keys: user_id, role_id, username.
    Raises 401 for invalid token, 404 for missing profile,
    403 for incomplete profile (NULL username) or unconfirmed email.
    """
    user = await resolve_token(authorization, supabase)
    user_id = user.user.id
    profile = await supabase.from_("profiles").select("role_id, username, email_confirmed").eq("id", user_id).single().execute()
    if not profile.data:
        raise HTTPException(status_code=404, detail="User profile not found")
    if profile.data["username"] is None:
        raise HTTPException(
            status_code=403,
            detail="Profile setup not complete — please complete account setup",
        )
    if not profile.data.get("email_confirmed"):
        raise HTTPException(
            status_code=403,
            detail="Email not confirmed — please verify your email before performing this action",
        )
    return {
        "user_id": user_id,
        "role_id": profile.data["role_id"],
        "username": profile.data["username"],
    }
```

---

## Task 4: Switch Three Endpoints from `resolve_token` to `resolve_user_profile`

**Files:**
- Modify: `users.py`

**Step 1: Update `PATCH /users/me/preferences`**

Change `update_my_preferences` to use `resolve_user_profile` instead of `resolve_token`:

```python
@router.patch("/me/preferences", response_model=PreferencesResponse)
async def update_my_preferences(
    body: UpdatePreferencesRequest,
    authorization: str = Header(...),
    supabase: AsyncClient = Depends(get_supabase),
):
    caller = await resolve_user_profile(authorization, supabase)
    return await _apply_preferences(supabase, caller["user_id"], body)
```

**Step 2: Update `PATCH /users/me/username`**

```python
@router.patch("/me/username", response_model=ChangeUsernameResponse)
@limiter.limit("5/minute")
async def change_my_username(request: Request, body: ChangeUsernameRequest, authorization: str = Header(...), supabase: AsyncClient = Depends(get_supabase)):
    caller = await resolve_user_profile(authorization, supabase)
    try:
        await supabase.from_("profiles").update({"username": body.username}).eq("id", caller["user_id"]).execute()
    except APIError:
        raise HTTPException(status_code=409, detail="Username already taken")
    return {"username": body.username}
```

**Step 3: Update `PATCH /users/me/email`**

```python
@router.patch("/me/email", status_code=200)
@limiter.limit("5/minute")
async def change_my_email(request: Request, body: ChangeEmailRequest, authorization: str = Header(...), supabase: AsyncClient = Depends(get_supabase)):
    caller = await resolve_user_profile(authorization, supabase)
    if not _is_valid_email(body.email):
        raise HTTPException(status_code=422, detail="Invalid email address")
    await supabase.auth.admin.update_user_by_id(caller["user_id"], {"email": body.email, "email_confirm": False})
    return {"message": f"Confirmation email sent to {body.email}"}
```

---

## Task 5: Run All Tests

**Step 1: Run the unconfirmed-user tests**

```
uv run pytest tests/test_unconfirmed.py -v
```

Expected: all 10 pass (6 blocked + 4 allowed).

**Step 2: Run the full test suite**

```
uv run pytest -v
```

Expected: 0 failures, no regressions.

---

## Task 6: Update Documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: `FRONTEND_API.md`
- Modify: `README.md`

**Step 1: Update `CLAUDE.md`**

- In `helpers.py` module description, note that `resolve_user_profile` now also checks `email_confirmed` and raises 403 if false
- In the **Authentication** section, add a note: "Unconfirmed users (email not verified) receive 403 on write endpoints. Read-only endpoints (`GET /users/me`, `GET /users`, `GET /users/me/matches`, `GET /users/me/matches/unconfirmed`) and `DELETE /users/me` remain accessible."
- In the **Error responses** section, add the new 403 detail string
- Update endpoint descriptions for `PATCH /users/me/preferences`, `PATCH /users/me/username`, `PATCH /users/me/email` to note they require confirmed email

**Step 2: Update `FRONTEND_API.md`**

- Add a section or note about 403 responses for unconfirmed users on write endpoints
- Update affected endpoint entries

**Step 3: Update `README.md`**

- Note that unconfirmed users are restricted to read-only access
