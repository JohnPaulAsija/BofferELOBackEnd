# Unconfirmed User Restrictions — Design Document

**Date:** 2026-04-03
**Status:** Approved

## Problem

Users who sign up but haven't confirmed their email can currently:
1. Appear in `GET /users` and `GET /users/top` (covered by existing plan 2026-03-29)
2. Perform write actions (report/confirm/reject matches, change username/email, update preferences)
3. Have their public profile and match history accessible via `GET /users/{user_id}` and `GET /users/{user_id}/matches`

## Scope

This design covers three related changes, each with its own implementation plan:

### A. Update existing plan (2026-03-29-exclude-pending-accounts)

- Fix Python version reference (3.11, not 3.10)
- Add test fixture compatibility note: `conftest.py` creates users with `email_confirm: True`, which sets `email_confirmed_at` on `auth.users`. Once the profile-creation trigger is updated (Task 4), test accounts automatically get `email_confirmed=TRUE` — no fixture changes needed.
- Add a verification step after Task 5 to confirm test accounts have `email_confirmed=TRUE`.

### B. Block unconfirmed users from acting

**Enforcement point:** `resolve_user_profile` in `helpers.py`.

Add `email_confirmed` to the profile SELECT. If `email_confirmed` is `False`, raise HTTP 403 with message: `"Email not confirmed -- please verify your email before performing this action"`.

This check goes after the existing NULL username check (both are 403, but different messages).

**Endpoints automatically blocked** (already use `resolve_user_profile`):
- `POST /matches` (report)
- `POST /matches/confirm`
- `POST /matches/reject`
- All admin endpoints

**Endpoints switched from `resolve_token` to `resolve_user_profile`:**
- `PATCH /users/me/preferences`
- `PATCH /users/me/username`
- `PATCH /users/me/email`

These gain one extra DB round trip (profile SELECT), acceptable because they are rate-limited write endpoints that hit the DB anyway.

**Endpoints that stay on `resolve_token` (allowed for unconfirmed):**
- `GET /users/me` — view own profile
- `GET /users` — read-only user list
- `GET /users/me/matches` — view own matches
- `GET /users/me/matches/unconfirmed` — view own pending matches
- `DELETE /users/me` — delete own account

**Error response:** 403, `{"detail": "Email not confirmed -- please verify your email before performing this action"}`

### C. Hide unconfirmed user profiles and match history

**Affected endpoints:**
- `GET /users/{user_id}` — add `.eq("email_confirmed", True)` to the profile query. Unconfirmed users return 404.
- `GET /users/{user_id}/matches` — add `.eq("email_confirmed", True)` to the profile existence check. Unconfirmed users return 404.

**Not affected** (already handled by plan A):
- `GET /users/top` — filtered by `email_confirmed`
- `GET /users` — filtered by `email_confirmed`

## Test Strategy

All plans are test-first. Tests use sacrificial pending users created with `email_confirm: False` via the admin API, cleaned up in `finally` blocks. Existing test fixtures (`conftest.py`) are unaffected because they create users with `email_confirm: True`.

## Dependencies

Plan B and C both depend on the `email_confirmed` column existing (Plan A, Tasks 2-5). Plan B and C are independent of each other and can be implemented in any order after Plan A's DB work is complete.
