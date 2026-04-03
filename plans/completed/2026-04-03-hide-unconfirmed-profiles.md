# Hide Unconfirmed User Profiles and Match History — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prevent public access to profiles and match history of users who haven't confirmed their email. `GET /users/{user_id}` and `GET /users/{user_id}/matches` should return 404 for unconfirmed users.

**Prerequisite:** The `email_confirmed` column and triggers from plan `2026-03-29-exclude-pending-accounts` must be applied first.

**Architecture:** Add `.eq("email_confirmed", True)` to the profile queries in both public endpoints. No new columns, triggers, or helpers needed.

**Tech Stack:** Python 3.11, FastAPI, Supabase, `supabase-py` async client, pytest-asyncio.

---

## Task 1: Write Failing Tests

**Files:**
- Create or modify: `tests/test_unconfirmed.py` (append to file from block-unconfirmed-users plan, or `tests/test_public.py` if that plan hasn't been implemented yet)

**Step 1: Add tests**

```python
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
```

**Step 2: Run the tests — expect both to FAIL**

```
uv run pytest tests/test_unconfirmed.py -k "unconfirmed_user_profile or unconfirmed_user_match_history" -v
```

Expected: 2 failures (profile and match history currently return 200).

---

## Task 2: Filter `GET /users/{user_id}` by `email_confirmed`

**Files:**
- Modify: `users.py` (around line 271-285)

**Step 1: Add the filter**

In `get_user_profile`, add `.eq("email_confirmed", True)` to the profile query:

```python
@router.get("/{user_id}", response_model=PublicUserProfileResponse)
async def get_user_profile(user_id: uuid.UUID, supabase: AsyncClient = Depends(get_supabase)):
    try:
        resp = (
            await supabase.from_("profiles")
            .select("id, username, elo, wins, losses, gender, preferredGame, preferredWeapon, preferredShield")
            .eq("id", str(user_id))
            .eq("email_confirmed", True)
            .single()
            .execute()
        )
    except APIError:
        raise HTTPException(status_code=404, detail="User not found")
    if not resp.data:
        raise HTTPException(status_code=404, detail="User not found")
    return {"user": resp.data}
```

---

## Task 3: Filter `GET /users/{user_id}/matches` by `email_confirmed`

**Files:**
- Modify: `users.py` (around line 288-323)

**Step 1: Add the filter to the profile existence check**

In `get_user_match_history`, add `.eq("email_confirmed", True)` to the profile check:

```python
try:
    profile_resp = await supabase.from_("profiles").select("id").eq("id", str(user_id)).eq("email_confirmed", True).single().execute()
except APIError:
    raise HTTPException(status_code=404, detail="User not found")
if not profile_resp.data:
    raise HTTPException(status_code=404, detail="User not found")
```

---

## Task 4: Run All Tests

**Step 1: Run the new tests**

```
uv run pytest tests/test_unconfirmed.py -k "unconfirmed_user_profile or unconfirmed_user_match_history" -v
```

Expected: both PASS.

**Step 2: Run the full test suite**

```
uv run pytest -v
```

Expected: 0 failures, no regressions. Existing tests for `GET /users/{user_id}` and `GET /users/{user_id}/matches` use confirmed test accounts and should be unaffected.

---

## Task 5: Update Documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: `FRONTEND_API.md`
- Modify: `README.md`

**Step 1: Update `CLAUDE.md`**

- Update `GET /users/{user_id}` endpoint description to note: "returns 404 for unconfirmed users"
- Update `GET /users/{user_id}/matches` endpoint description to note: "returns 404 for unconfirmed users"

**Step 2: Update `FRONTEND_API.md`**

- Update both endpoint entries to note that unconfirmed users return 404

**Step 3: Update `README.md`**

- If relevant, note that unconfirmed user profiles are not publicly accessible
