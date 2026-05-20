# Prod-Safe Integration Test Suite Re-Engineering — Implementation Plan

> **For Claude:** This is a planning document only. Do not start implementation
> without an explicit go-ahead. Implementation will require interacting with the
> live production Supabase project (`Boffer_ELO`, ref `xzwwpkjfnnmmepuvnelx`),
> so each task must land behind a careful review of what data it could touch.

**Status:** Not started. Captured as a follow-up to the deletion of the
dedicated test Supabase branch (see commit `docs: reflect deletion of Supabase
test branch and pause integration tests`).

---

## Context

The dedicated test Supabase branch was deleted to cut cost. Integration tests
are currently paused: `test.env` is not populated, and the existing
`reset_and_seed` fixture would wipe production data if pointed at the live
project today.

The goal of this plan is to re-engineer the integration test suite so that it
can run safely against the production `Boffer_ELO` Supabase project —
touching only data it created, never deleting anything else, and not requiring
a persistent privileged test account in prod.

Two concerns are addressed together because they share the same surface area
(`initialize.py`, `tests/conftest.py`, `admin.py`):

1. **Make the destructive flow non-destructive.** Remove the dependency on
   `POST /admin/reset` and the four fixed `TEST_USER*_EMAIL` env vars. Use
   per-run UUID-namespaced accounts and surgical teardown.
2. **Fix the pre-existing import-time `API_URL` crash.** `initialize.py:18`
   calls `create_client()` at module load, which raises `KeyError: 'API_URL'`
   any time `initialize.py` is imported without env vars set — including
   `uv run pytest tests/test_helpers.py tests/test_rate_limit.py`, which the
   docs claim runs standalone. Both unit-only and integration runs benefit
   from making this lazy.

---

## Architecture changes

- `initialize.py` no longer creates a sync client at module load. Callers that
  need it (`seed_data.py`, the conftest fixture) call `create_client()`
  themselves when they need an instance.
- `tests/conftest.py`'s `reset_and_seed` fixture:
  - Generates a per-run namespace `run_id = uuid4().hex[:8]` and uses it to
    build emails like `test_<run_id>_user1@bofferelo-test.invalid`.
  - Tracks every auth user ID and match ID it creates in a session-scoped
    registry.
  - Teardown (`yield`-based) deletes only the tracked IDs. No global reset.
  - Promotes one of the per-run users to superAdmin in the DB (`role_id = 3`)
    by direct service-role write rather than relying on a persistent
    privileged account.
- `POST /admin/reset` (in `admin.py`) is removed entirely. The endpoint exists
  only to serve the destructive fixture and is unsafe to ship in any image
  that might point at prod. `seed_users`/`seed_matches` admin endpoints stay
  (they're not destructive).
- `test.env.example` and the `claude.md` Test Environment section are
  rewritten to reflect the new model: no fixed test users, no `SUPER_ADMIN_*`
  vars in `test.env` (the superAdmin is created and destroyed per run).

**Tech stack:** Python 3.11, FastAPI, Supabase (`supabase-py` async +
service-role sync client), pytest-asyncio. No new dependencies.

---

## Task 1 — Make sync client creation lazy (fixes Issue 2)

**Files:**
- `initialize.py`
- `seed_data.py`
- `tests/conftest.py:77,229` (already imports `create_client` — verify)
- `claude.md:81` (architecture description mentions `client` is created at
  import time — update to "lazy")

**Steps:**

1. In `initialize.py`, delete the module-scope `client = create_client()` line
   (currently line 18). Keep `create_client()` exported.
2. In `seed_data.py`, change `from initialize import client` to
   `from initialize import create_client` and instantiate a module-local
   `client = create_client()` inside the `if __name__ == "__main__":` block
   (and at the top of any function that uses it — preferred, so importing
   `seed_data` as a library doesn't require env vars either).
3. Verify nothing else in the repo does `from initialize import client`:
   `grep -rn "from initialize import client" --include="*.py"`.
4. **Verification:** `uv run pytest tests/test_helpers.py tests/test_rate_limit.py`
   with no `test.env` and no `.env` present should now pass cleanly. Currently
   it crashes with `KeyError: 'API_URL'`.

**Why this is safe on its own:** This change is orthogonal to the prod-safety
work. Land it first — it cleans up the unit-test path and unblocks the rest
of the plan.

---

## Task 2 — Per-run namespacing in `reset_and_seed`

**Files:**
- `tests/conftest.py:59–174` (rewrite the fixture body)
- `test.env.example` (drop the four fixed `TEST_USER*_EMAIL` vars and the
  `SUPER_ADMIN_*` vars; document the new minimal var set)

**Steps:**

1. Replace the four fixed `TEST_USER*_EMAIL` env-var reads with a
   `run_id = uuid.uuid4().hex[:8]` and synthesize emails like
   `f"test_{run_id}_{role}@bofferelo-test.invalid"`. The `.invalid` TLD is
   reserved by RFC 2606 — Supabase will accept it but it can't accidentally
   email a real user.
2. Initialize a session registry: `created = {"auth_user_ids": [], "match_ids": []}`.
3. **Drop the `POST /admin/reset` call entirely.** No deletion of pre-existing
   data, ever.
4. Create the 4 test users (3 regular + 1 admin) plus a 5th per-run
   superAdmin (instead of signing into a persistent one). Append every UUID
   to `created["auth_user_ids"]`. Promote the admin user with
   `role_id = 2` and the superAdmin with `role_id = 3` via direct
   service-role writes to `profiles`.
5. When seeding the 3 confirmed + 2 unconfirmed matches, capture the returned
   match IDs and append to `created["match_ids"]`.
6. Convert the fixture from `return result` to `yield result` and add a
   teardown block:
   ```python
   try:
       yield result
   finally:
       for mid in created["match_ids"]:
           sync_client.from_("Matches").delete().eq("id", mid).execute()
       for uid in created["auth_user_ids"]:
           try:
               sync_client.auth.admin.delete_user(uid)
           except Exception:
               pass  # best-effort; profile FKs cascade via existing trigger
   ```
   The existing `before_profile_delete` trigger handles match-FK reassignment
   to the sentinel, so deleting auth users is sufficient for profile cleanup.
7. Drop `SUPER_ADMIN_EMAIL` and `SUPER_ADMIN_PASSWORD` from `test.env`. The
   per-run superAdmin is created from scratch each time.

**Note on the `[deleted]` sentinel user:** the production project already has
the sentinel UUID profile (`DELETED_USER_SENTINEL_ID` in `helpers.py`). The
fixture must never delete it. Since the fixture only deletes IDs in its own
registry, this is automatic — but add an `assert uid != DELETED_USER_SENTINEL_ID`
guard before each delete as a belt-and-suspenders.

---

## Task 3 — Tests that create extra users must self-clean

**Files:**
- `tests/test_unconfirmed.py` — already uses `try/finally` with
  `sync_supabase.auth.admin.delete_user(...)`. Audit for completeness.
- `tests/test_account.py` — creates sacrificial users for destructive
  delete tests. Audit each one for guaranteed cleanup.
- Any other test that uses `sync_supabase` to create entities.

**Steps:**

1. `grep -rn "sync_supabase\|admin.create_user\|admin.delete_user" tests/`
   and walk every match. For each test that creates an entity, confirm it has
   a `try/finally` (or fixture-based) teardown that deletes that entity even
   on assertion failure.
2. Where a test creates a user but doesn't clean up, wrap in `try/finally`
   or convert to a function-scoped fixture with `yield`.
3. Add a `pytest_sessionfinish` hook in `conftest.py` that emits a warning
   listing any auth users whose email starts with `test_` and matches the
   current `run_id` — a safety net in case the fixture registry missed
   something.

---

## Task 4 — Remove `POST /admin/reset`

**Files:**
- `admin.py:100–115` (delete the endpoint and the bootstrap-email lookup)
- `claude.md:127` (remove the endpoint from the API documentation)
- `FRONTEND_API.md:1003–1027` (remove the endpoint section)
- `users.py:269` (review — also reads `SUPER_ADMIN_EMAIL`; this read becomes
  dead code if `/admin/reset` is the only caller)

**Steps:**

1. Delete the `/admin/reset` endpoint and the `_reset_data` body.
2. Drop `SUPER_ADMIN_EMAIL` from the required env-var documentation
   (`claude.md:298`) — it's no longer read by the server.
3. If `users.py:269` is the only other read, delete that branch too. If
   there's another use, leave it but reword the env-var docs.
4. Update `FRONTEND_API.md` to remove the endpoint and any references to it.
5. Update `claude.md`'s `### Test Environment` section to reflect the new
   flow.

**Why this is safe:** with Task 2 in place, the fixture no longer calls
`/admin/reset`. Production code never called it (it was test infrastructure
only). Removing it eliminates a footgun.

---

## Task 5 — Wire up CI-friendly defaults

**Files:**
- `test.env.example`
- `claude.md`

**Steps:**

1. Document the new minimal `test.env`:
   ```env
   API_URL=https://xzwwpkjfnnmmepuvnelx.supabase.co
   API_KEY_s=<service-role-key-for-prod>
   TEST_PASSWORD=<any-password>   # used for all per-run test users
   ```
   No `TEST_USER*_EMAIL`. No `SUPER_ADMIN_*`. Way fewer footguns.
2. Add a top-of-file warning to `test.env.example` that the service role key
   in this file is for the **production** Supabase project and must never be
   checked into version control or shared.
3. Update `claude.md`'s Test Environment section to describe the per-run
   namespacing model and remove the "Paused" banner from the
   doc-only PR landed today.

---

## Task 6 — End-to-end verification

1. **Unit-only path:** `uv run pytest tests/test_helpers.py tests/test_rate_limit.py`
   with no `test.env` — must pass after Task 1.
2. **No-test-env path:** `uv run pytest` with no `test.env` — integration
   tests must skip cleanly via the existing `pytest.skip("no test.env …")`
   guards; unit tests must still pass.
3. **Full path against prod:** populate `test.env` with prod creds and run
   `uv run pytest`. Before/after the run, verify the prod project's
   `Matches` row count and `auth.users` count are unchanged outside the
   `test_<run_id>_` prefix. Spot-check via the Supabase MCP `execute_sql`
   tool:
   ```sql
   SELECT COUNT(*) FROM "Matches";
   SELECT COUNT(*) FROM auth.users WHERE email NOT LIKE 'test_%@bofferelo-test.invalid';
   ```
4. **Failure-mode test:** intentionally fail one test (e.g. assert False) and
   confirm teardown still runs — the per-run users and matches must still be
   deleted.
5. **Reset endpoint gone:** `grep -rn "/admin/reset" --include="*.py"` returns
   only references in deleted-line context (none).

---

## Risks and open questions

1. **Service-role key in `test.env` is the prod key.** A leaked `test.env`
   = full prod compromise. Mitigations: `test.env` stays gitignored;
   `.gitignore` already covers it; document the risk loudly in
   `test.env.example`.
2. **Per-run user creation hits Supabase Auth quota.** Each run creates ~5
   auth users. At a few runs per day that's negligible; in a CI loop it
   could add up. Worth checking the project's auth-user soft cap before
   wiring this into CI.
3. **`auth.admin.delete_user` is async-eventually-consistent** in some
   Supabase versions. If teardown races a subsequent test, the new user
   might collide on email. The UUID-namespaced emails make collisions
   essentially impossible, but if we ever see flakiness, add a small
   verification poll in teardown.
4. **`reset_and_seed` is `autouse=True`.** Even tests that don't need it
   pay the per-run setup cost. Consider downgrading to `autouse=False` and
   making tests opt in via explicit fixture parameters — but that's a
   broader refactor and out of scope here.
5. **No CI configured today** (no `.github/workflows/`). This plan doesn't
   add CI; it just makes integration tests safe to add to CI later.

---

## Out of scope

- Setting up GitHub Actions / any CI provider. Tracked separately.
- Migrating to a fully isolated test schema (Postgres `CREATE SCHEMA
  test_<run_id>`). Over-engineered for this codebase; revisit if the
  namespacing approach proves leaky.
- Replacing integration tests with mocked-Supabase unit tests. Different
  tradeoff; not what was asked for.
- Restoring a dedicated test Supabase branch. The whole point of this plan
  is to make that unnecessary.
