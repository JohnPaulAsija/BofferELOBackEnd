# Exclude Pending Accounts from User-Facing Endpoints Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prevent unconfirmed (pending) Supabase accounts from appearing in `GET /users` and `GET /users/top`.

**Architecture:** Add an `email_confirmed` boolean column to the `profiles` table. Keep it in sync via two DB triggers: one fired when the profile is first created (handles admin-created users with `email_confirm: True`), and one fired when the `auth.users` row is updated (handles users who click the confirmation link). Filter both affected endpoints by `email_confirmed = TRUE`. This is a pure DB + query-filter change with no new endpoints and no breaking API contract changes.

**Tech Stack:** Python 3.11, FastAPI, Supabase (PostgREST + GoTrue), `supabase-py` async client, pytest-asyncio, Supabase MCP (`apply_migration`, `execute_sql`).

---

## Background

When a user signs up, Supabase inserts a row into `auth.users` and immediately fires the profile-creation trigger — before the user confirms their email. If the user provided a `username` in `user_metadata`, the trigger sets it in `profiles`. Because `GET /users` and `GET /users/top` only filter `username IS NOT NULL`, those pending accounts leak through.

The test `test_list_users_excludes_pending_accounts` (added in the bug-discovery session) currently **fails**, confirming the bug. `test_leaderboard_excludes_pending_accounts` currently passes only because the leaderboard is cached; the underlying data issue is the same.

---

## Task 1: Inspect the Profile-Creation Trigger

Before touching anything, read the current trigger so subsequent migrations can extend (not replace) it correctly.

**Files:** none (read-only DB inspection)

**Step 1: Query the trigger definition**

Run via `execute_sql` (Supabase MCP):

```sql
SELECT trigger_name, event_manipulation, action_timing, action_statement
FROM information_schema.triggers
WHERE event_object_schema = 'auth'
  AND event_object_table = 'users'
ORDER BY trigger_name;
```

Expected: one or more rows. Note the **trigger name** and **function name** for the profile-creation trigger (typically something like `on_auth_user_created`).

**Step 2: Read the trigger function body**

```sql
SELECT prosrc
FROM pg_proc
WHERE proname = '<function_name_from_step_1>';
```

Replace `<function_name_from_step_1>` with the actual function name. Read the body carefully — specifically:
- What columns does it INSERT into `profiles`?
- Does it already reference `NEW.email_confirmed_at`?
- Does it reference `NEW.raw_user_meta_data`?

Record the answers. The next task extends this function.

---

## Task 2: Add `email_confirmed` Column

**Files:**
- Migration applied via Supabase MCP only — no Python files change here.

**Step 1: Apply the migration**

Via `apply_migration` (Supabase MCP), migration name `add_email_confirmed_to_profiles`:

```sql
ALTER TABLE profiles
  ADD COLUMN email_confirmed BOOLEAN NOT NULL DEFAULT FALSE;
```

**Step 2: Verify**

```sql
SELECT column_name, data_type, column_default, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name   = 'profiles'
  AND column_name  = 'email_confirmed';
```

Expected: one row, `data_type = boolean`, `column_default = false`, `is_nullable = NO`.

---

## Task 3: Backfill Existing Confirmed Users

All users created before the migration have `email_confirmed = FALSE`. Backfill them now so existing test accounts work after the query filters are added.

**Step 1: Apply the migration**

Via `apply_migration`, migration name `backfill_email_confirmed`:

```sql
UPDATE public.profiles p
SET    email_confirmed = TRUE
FROM   auth.users u
WHERE  u.id = p.id
  AND  u.email_confirmed_at IS NOT NULL;
```

**Step 2: Verify**

```sql
SELECT COUNT(*) AS confirmed_count
FROM   public.profiles p
JOIN   auth.users u ON u.id = p.id
WHERE  p.email_confirmed = TRUE
  AND  u.email_confirmed_at IS NOT NULL;
```

And check nothing was missed:

```sql
SELECT COUNT(*) AS missed_count
FROM   public.profiles p
JOIN   auth.users u ON u.id = p.id
WHERE  p.email_confirmed = FALSE
  AND  u.email_confirmed_at IS NOT NULL;
```

Expected: `missed_count = 0`.

---

## Task 4: Update the Profile-Creation Trigger to Set `email_confirmed`

This handles the case where an admin creates a user with `email_confirm: True` — the `auth.users` INSERT already has `email_confirmed_at IS NOT NULL`, so the profile should be born with `email_confirmed = TRUE`.

**Files:**
- Migration applied via Supabase MCP only.

**Step 1: Read the current trigger function body (from Task 1)**

Find the INSERT into `profiles`. It will look something like:

```sql
INSERT INTO public.profiles (id, username, elo, wins, losses, ...)
VALUES (NEW.id, NEW.raw_user_meta_data->>'username', 1000, 0, 0, ...);
```

**Step 2: Apply the migration**

Via `apply_migration`, migration name `profile_creation_sets_email_confirmed`.

Reconstruct the function with `email_confirmed` added to the INSERT. The exact SQL depends on what you read in Task 1, but the pattern is:

```sql
CREATE OR REPLACE FUNCTION <existing_function_name>()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = 'public'
AS $$
BEGIN
  INSERT INTO public.profiles (
    id,
    -- ... all existing columns ...,
    email_confirmed
  )
  VALUES (
    NEW.id,
    -- ... all existing values ...,
    (NEW.email_confirmed_at IS NOT NULL)   -- TRUE for admin-confirmed users
  );
  RETURN NEW;
END;
$$;
```

> **Important:** Copy every existing column verbatim from the body you read in Task 1. Only add `email_confirmed` — do not alter any other column.

**Step 3: Verify with a new admin-created user**

```sql
-- Create a throwaway user with email_confirm = true (admin path)
-- Then check their profile
SELECT p.email_confirmed, u.email_confirmed_at IS NOT NULL AS auth_confirmed
FROM   public.profiles p
JOIN   auth.users u ON u.id = p.id
ORDER BY p.id DESC
LIMIT 5;
```

Both columns should agree for recently-created users.

---

## Task 5: Add UPDATE Trigger for Email Confirmation

This handles the normal user flow: they sign up (pending), then click the confirmation link (which sets `email_confirmed_at` on their `auth.users` row via an UPDATE).

**Files:**
- Migration applied via Supabase MCP only.

**Step 1: Apply the migration**

Via `apply_migration`, migration name `sync_email_confirmed_on_auth_update`:

```sql
-- Trigger function
CREATE OR REPLACE FUNCTION public.sync_email_confirmed_on_update()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = 'public'
AS $$
BEGIN
  -- Only act when email_confirmed_at transitions from NULL → non-NULL
  IF NEW.email_confirmed_at IS NOT NULL AND OLD.email_confirmed_at IS NULL THEN
    UPDATE public.profiles
    SET    email_confirmed = TRUE
    WHERE  id = NEW.id;
  END IF;
  RETURN NEW;
END;
$$;

-- Trigger
CREATE TRIGGER on_auth_user_email_confirmed
  AFTER UPDATE ON auth.users
  FOR EACH ROW
  EXECUTE FUNCTION public.sync_email_confirmed_on_update();
```

**Step 2: Verify trigger exists**

```sql
SELECT trigger_name, event_manipulation, action_timing
FROM information_schema.triggers
WHERE event_object_schema = 'auth'
  AND event_object_table  = 'users'
  AND trigger_name        = 'on_auth_user_email_confirmed';
```

Expected: one row.

---

## Task 6: Filter `GET /users` by `email_confirmed`

**Files:**
- Modify: `users.py:37-45`

**Step 1: Write the failing test**

The test `test_list_users_excludes_pending_accounts` in `tests/test_users.py` is already written and currently failing. Run it now to confirm the starting state:

```
uv run pytest tests/test_users.py::test_list_users_excludes_pending_accounts -v
```

Expected: **FAIL** — `AssertionError: Pending (unconfirmed email) user must not appear in user list`

**Step 2: Apply the filter**

In `users.py`, find `list_users` (around line 33). Add `.eq("email_confirmed", True)` to the profiles query:

```python
@router.get("", response_model=UsersListResponse)
async def list_users(authorization: str = Header(...), supabase: AsyncClient = Depends(get_supabase)):
    user = await resolve_token(authorization, supabase)
    user_id = user.user.id
    result = (
        await supabase.from_("profiles")
        .select("id, username")
        .neq("id", user_id)
        .not_.is_("username", "null")
        .eq("email_confirmed", True)
        .order("username", desc=False)
        .execute()
    )
    return {"users": result.data}
```

**Step 3: Run the failing test**

```
uv run pytest tests/test_users.py::test_list_users_excludes_pending_accounts -v
```

Expected: **PASS**

**Step 4: Run the full user-list suite**

```
uv run pytest tests/test_users.py -v
```

Expected: all pass (no regressions).

---

## Task 7: Filter `GET /users/top` by `email_confirmed`

**Files:**
- Modify: `users.py:48-60`

**Step 1: Confirm the leaderboard test's true state**

The leaderboard pending-account test currently passes due to caching. After the cache expires (or in a fresh test session), it would fail. Run the test in isolation (no preceding leaderboard call) to confirm:

```
uv run pytest tests/test_public.py::test_leaderboard_excludes_pending_accounts -v
```

Note the result for the record.

**Step 2: Apply the filter**

In `users.py`, find `get_leaderboard` (around line 48). Add `.eq("email_confirmed", True)`:

```python
@router.get("/top", response_model=LeaderboardResponse)
@cache(expire=60, namespace="leaderboard")
async def get_leaderboard(supabase: AsyncClient = Depends(get_supabase)):
    result = (
        await supabase.from_("profiles")
        .select("id, username, elo, wins, losses")
        .not_.is_("username", "null")
        .neq("id", DELETED_USER_SENTINEL_ID)
        .eq("email_confirmed", True)
        .order("elo", desc=True)
        .limit(100)
        .execute()
    )
    return {"leaderboard": result.data}
```

**Step 3: Run the leaderboard suite**

```
uv run pytest tests/test_public.py -k "leaderboard" -v
```

Expected: all leaderboard tests pass.

---

## Task 8: Run the Full Test Suite

**Step 1: Unit tests**

```
uv run pytest tests/test_helpers.py tests/test_rate_limit.py -v
```

Expected: 16 passed.

**Step 2: Integration tests**

```
uv run pytest tests/test_public.py tests/test_users.py tests/test_account.py -v
```

Expected: 0 failed. Previously-failing `test_list_users_excludes_pending_accounts` now passes. Previously cache-masked `test_leaderboard_excludes_pending_accounts` now passes unconditionally.

---

## Task 9: Update Documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: `FRONTEND_API.md`
- Modify: `README.md`

**Step 1: Update `CLAUDE.md`**

In the **Architecture** section, update the `profiles` table description to mention the new column and both triggers:
- `email_confirmed` — BOOLEAN, default FALSE; set to TRUE by the profile-creation trigger when `email_confirmed_at IS NOT NULL` at signup, or by the `on_auth_user_email_confirmed` trigger when the user confirms their email
- Note that `GET /users` and `GET /users/top` now also filter `email_confirmed = TRUE`

In the **API endpoints** section, update the descriptions for:
- `GET /users/top` — add "only includes users who have confirmed their email"
- `GET /users` — add "only includes users who have confirmed their email"

In the **Postgres Functions** section or a new **DB Triggers** section, document `sync_email_confirmed_on_update`.

**Step 2: Update `FRONTEND_API.md`**

Update the `GET /users/top` and `GET /users` endpoint entries to note that pending (unconfirmed email) accounts are excluded.

**Step 3: Update `README.md`**

If the README lists features or data filters, note that leaderboard and user list exclude unconfirmed accounts.
