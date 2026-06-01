# Prod-Safe Integration Test Suite Re-Engineering — Implementation Plan

> **For Claude:** This is a planning document only. Do not start implementation
> without an explicit go-ahead. Implementation will require interacting with the
> live production Supabase project (`Boffer_ELO`, ref `xzwwpkjfnnmmepuvnelx`),
> so each task must land behind a careful review of what data it could touch.

**Status:** Implemented and **verified against prod (`Boffer_ELO`) on 2026-06-01.**
Tasks 1–5 landed in commits `Make Supabase client init lazy…`,
`Re-engineer integration test suite for prod safety; remove /admin/reset`, and
`Consolidate test.env into .env…`. Task 6 verification surfaced real defects
that were then fixed (see Verification log below). Final state: full suite
**119 passed, 0 failed** against prod; `Matches` (1,771) and `auth.users` (197)
counts unchanged before/after the run, including a teardown-under-failure run.

### Verification log (2026-06-01)

The first full prod run was **not** all-green — it exposed defects the remote
implementor could not have caught without prod credentials:

1. **Dead bootstrap guard + obsolete test.** Task 4 retired the
   `SUPER_ADMIN_EMAIL` bootstrap-superAdmin concept, but `DELETE /users/{user_id}`
   (`users.py`) still held the now-unreachable guard, and
   `test_delete_user_bootstrap_blocked` still asserted a 400 block. With
   `SUPER_ADMIN_EMAIL` unset the guard never fired, so the test **deleted the
   per-run superAdmin mid-session**, invalidating `super_admin_token` and
   cascading into 5 failures. **Fix (per user decision — retire fully):** removed
   the guard and `import os` from `users.py`, deleted the obsolete test, and
   purged bootstrap-superAdmin references from `claude.md`, `FRONTEND_API.md`,
   and `.env.example`.
2. **Sacrificial-user teardown gap.** `test_delete_user_success` /
   `test_delete_user_verify_gone` relied on the (broken) DELETE endpoint to
   remove their own users — no `try/finally`. **Fix:** added `try/finally`
   cleanup; switched `_create_sacrificial_user` to the reserved
   `@bofferelo-test.invalid` domain; broadened the `pytest_sessionfinish`
   leak-check to flag any `@bofferelo-test.invalid`/`@test.com` survivor.
3. **Untracked match leak.** Matches created by individual tests
   (`test_report_match_with_rule_set_id`, `test_delete_me_match_history_preserved`)
   were preserved against the sentinel by the `before_profile_delete` trigger and
   never cleaned up — the fixture only deleted matches in its own registry.
   **Fix:** the fixture teardown now deletes matches by membership in the per-run
   user set (winnerId/loserId/reporterId) *before* deleting users; both tests
   also clean up their own match.

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

Three concerns are addressed together because they share the same surface area
(`initialize.py`, `tests/conftest.py`, `admin.py`, and the env-file layout):

1. **Make the destructive flow non-destructive.** Remove the dependency on
   `POST /admin/reset` and the four fixed `TEST_USER*_EMAIL` env vars. Use
   per-run UUID-namespaced accounts and surgical teardown.
2. **Fix the pre-existing import-time `API_URL` crash.** `initialize.py:18`
   calls `create_client()` at module load, which raises `KeyError: 'API_URL'`
   any time `initialize.py` is imported without env vars set — including
   `uv run pytest tests/test_helpers.py tests/test_rate_limit.py`, which the
   docs claim runs standalone. Both unit-only and integration runs benefit
   from making this lazy.
3. **Collapse `test.env` into `.env`.** Once tests run against prod, the two
   files would hold the same `API_URL` and service-role key — two copies of
   the same secret means double the leak surface for zero benefit. Delete
   `test.env` and `test.env.example` entirely; the app already uses `.env`
   for its own secrets. Replace the "presence of `test.env`" gate (which
   today is what makes integration tests skip when absent) with an explicit
   opt-in env var, `RUN_INTEGRATION_TESTS=1`, so the default `pytest` run
   stays unit-tests-only even when `.env` is fully populated.

---

## Architecture changes

- `initialize.py` no longer creates a sync client at module load. Callers that
  need it (`seed_data.py`, the conftest fixture) call `create_client()`
  themselves when they need an instance.
- `tests/conftest.py`'s `reset_and_seed` fixture:
  - Loads `.env` (not `test.env` — that file is gone) at session start.
  - Gates on `RUN_INTEGRATION_TESTS=1`. Unset → skip integration tests
    cleanly. Set → run them against whatever `API_URL` points to.
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
- **`test.env` and `test.env.example` are deleted.** All credentials live in
  `.env` (production single-source-of-truth). The `claude.md` Test
  Environment section is rewritten to describe the consolidated model.

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
- `tests/conftest.py:18` — change `load_dotenv("test.env")` to `load_dotenv()`
  so `.env` is the single source.
- `tests/conftest.py:59–174` (rewrite the fixture body)

**Steps:**

1. At the top of the fixture, gate on the opt-in flag:
   ```python
   if os.environ.get("RUN_INTEGRATION_TESTS") != "1":
       yield {}
       return
   ```
   No env var → skip cleanly, no DB contact, no namespace generation.
2. Replace the four fixed `TEST_USER*_EMAIL` env-var reads with a
   `run_id = uuid.uuid4().hex[:8]` and synthesize emails like
   `f"test_{run_id}_{role}@bofferelo-test.invalid"`. The `.invalid` TLD is
   reserved by RFC 2606 — Supabase will accept it but it can't accidentally
   email a real user.
3. Initialize a session registry: `created = {"auth_user_ids": [], "match_ids": []}`.
4. **Drop the `POST /admin/reset` call entirely.** No deletion of pre-existing
   data, ever.
5. Create the 4 test users (3 regular + 1 admin) plus a 5th per-run
   superAdmin (instead of signing into a persistent one). Append every UUID
   to `created["auth_user_ids"]`. Promote the admin user with
   `role_id = 2` and the superAdmin with `role_id = 3` via direct
   service-role writes to `profiles`.
6. When seeding the 3 confirmed + 2 unconfirmed matches, capture the returned
   match IDs and append to `created["match_ids"]`.
7. Convert the fixture from `return result` to `yield result` and add a
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

**Note on the `[deleted]` sentinel user:** the production project already has
the sentinel UUID profile (`DELETED_USER_SENTINEL_ID` in `helpers.py`). The
fixture must never delete it. Since the fixture only deletes IDs in its own
registry, this is automatic — but add an `assert uid != DELETED_USER_SENTINEL_ID`
guard before each delete as a belt-and-suspenders.

---

## Task 3 — Tests that create extra users must self-clean + replace skip guards

**Files:**
- `tests/test_unconfirmed.py` — already uses `try/finally` with
  `sync_supabase.auth.admin.delete_user(...)`. Audit for completeness.
- `tests/test_account.py` — creates sacrificial users for destructive
  delete tests. Audit each one for guaranteed cleanup.
- `tests/test_public.py`, `tests/test_users.py`, `tests/test_unconfirmed.py`,
  `tests/test_account.py` — every `pytest.skip("no test.env …")` guard needs
  to be updated to check `RUN_INTEGRATION_TESTS` instead. The cleanest
  approach is to centralize this in a single fixture in `conftest.py` (e.g.
  `integration_only`) that calls `pytest.skip(...)` when the flag is unset,
  and have integration tests depend on it.
- Any other test that uses `sync_supabase` to create entities.

**Steps:**

1. `grep -rn "sync_supabase\|admin.create_user\|admin.delete_user" tests/`
   and walk every match. For each test that creates an entity, confirm it has
   a `try/finally` (or fixture-based) teardown that deletes that entity even
   on assertion failure.
2. Where a test creates a user but doesn't clean up, wrap in `try/finally`
   or convert to a function-scoped fixture with `yield`.
3. Replace every `pytest.skip("no test.env …")` line. Recommended pattern:
   add an `integration_only` fixture in `conftest.py` that does
   `if os.environ.get("RUN_INTEGRATION_TESTS") != "1": pytest.skip("integration tests opt-in via RUN_INTEGRATION_TESTS=1")`,
   then sprinkle it into the integration tests in place of the manual
   `if sync_supabase is None: pytest.skip(...)` checks. Cleaner and one
   place to change the gate.
4. Add a `pytest_sessionfinish` hook in `conftest.py` that emits a warning
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

## Task 5 — Delete `test.env*` and consolidate on `.env`

**Files:**
- `test.env.example` (DELETE)
- Any `.env.example` if one exists (verify with `ls -la`); otherwise create
  one documenting the consolidated var set.
- `.gitignore:132` — drop the explicit `test.env` line (the file is gone;
  `.env` is already covered).
- `README.md`, `claude.md`, `FRONTEND_API.md` — purge every remaining
  reference to `test.env` and replace with `.env` + `RUN_INTEGRATION_TESTS`.

**Steps:**

1. `git rm test.env.example`.
2. If a `.env.example` doesn't already exist, create one documenting the full
   consolidated var set:
   ```env
   # Required (server + integration tests)
   API_URL=https://xzwwpkjfnnmmepuvnelx.supabase.co
   API_KEY_s=<service-role-key-for-prod>

   # Optional
   HOST=0.0.0.0
   PORT=8000
   CORS_ORIGINS=                  # comma-separated additional CORS origins
   TEST_PASSWORD=TestPassword123! # password assigned to per-run test users

   # Integration test opt-in (default: integration tests skip).
   # Set to 1 ONLY when you want pytest to create + tear down test users
   # against whatever Supabase project API_URL points at.
   RUN_INTEGRATION_TESTS=0
   ```
3. Add a prominent top-of-file warning to `.env.example` that the service
   role key is full-access prod credentials — never check in, never share,
   rotate immediately if leaked.
4. Remove the `test.env` line from `.gitignore`. `.env` should already be
   gitignored (verify); if not, add it.
5. `grep -rn "test\.env" --include="*.md" --include="*.py" .` and update
   every remaining hit. The conftest's `load_dotenv("test.env")` call from
   Task 2 is already handled; this step catches the docs.
6. Update `claude.md`'s Test Environment section to describe the
   consolidated model: one `.env`, opt-in via `RUN_INTEGRATION_TESTS=1`,
   per-run namespacing. Remove the "Paused" banner that landed in the
   doc-only PR.

---

## Task 6 — End-to-end verification

1. **Unit-only path, no env file:** `uv run pytest tests/test_helpers.py tests/test_rate_limit.py`
   with no `.env` and no env vars set — must pass after Task 1.
2. **Default path with `.env` present but no opt-in:** `RUN_INTEGRATION_TESTS`
   unset → `uv run pytest` runs unit tests, integration tests skip cleanly.
   This is the default a contributor hits after `cp .env.example .env`.
3. **Full path against prod:** set `RUN_INTEGRATION_TESTS=1` and
   `uv run pytest`. Before/after the run, verify the prod project's
   `Matches` row count and non-test `auth.users` count are unchanged. Spot
   check via the Supabase MCP `execute_sql` tool:
   ```sql
   SELECT COUNT(*) FROM "Matches";
   SELECT COUNT(*) FROM auth.users WHERE email NOT LIKE 'test_%@bofferelo-test.invalid';
   ```
4. **Failure-mode test:** with `RUN_INTEGRATION_TESTS=1`, intentionally fail
   one integration test (e.g. `assert False`) and confirm teardown still
   runs — the per-run users and matches must still be deleted. Re-run the
   spot-check SQL.
5. **Reset endpoint gone:** `grep -rn "/admin/reset" --include="*.py"` returns
   only references in deleted-line context (none).
6. **No `test.env` references remain:** `grep -rn "test\.env" .` returns no
   hits in tracked files (the `plans/completed/*` historical files are
   acceptable since they document a prior state).

---

## Risks and open questions

1. **One secret file, one leak surface.** `.env` now holds the only copy of
   the prod service-role key for both the app and the test suite. Mitigations:
   `.env` stays gitignored (verify in Task 5); document the risk loudly in
   `.env.example`; rotate the service-role key in the Supabase dashboard
   immediately if anyone suspects a leak. The opt-in flag means even a
   leaked `.env` won't auto-run destructive integration tests unless the
   attacker also flips `RUN_INTEGRATION_TESTS=1`.
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
