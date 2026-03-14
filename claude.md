# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

Backend API for a React Native ELO ranking app for **boffer combat games** (e.g. Dagorhir, Belegarth) — medieval LARP combat sports using foam weapons.

Core features:
- **ELO calculation** — compute and update player ratings after each confirmed match
- **Rankings** — return a sorted leaderboard of players by ELO
- **Match history** — show per-player match records

The Supabase `Matches` table is the primary data source. `confirmedAt` being NULL means a match is pending/unconfirmed; non-NULL means it is confirmed and should count toward ELO.

## Git

**Never run any git commands.** All commits, branches, and version control operations are handled by the user. Do not run `git add`, `git commit`, `git push`, `git stash`, `git checkout`, or any other git command under any circumstance.

## Commands

This project uses `uv` for dependency management (Python 3.10).

```bash
# Install dependencies
uv sync

# Run the server
uv run python main.py

# Run the FastAPI app directly
uv run python api.py

# Run all tests (unit + integration)
uv run pytest

# Run only unit tests (no Supabase needed)
uv run pytest tests/test_helpers.py tests/test_rate_limit.py

# Run only integration tests (requires test.env)
uv run pytest tests/test_public.py tests/test_users.py
```

### Test Environment

Integration tests require a `test.env` file with credentials for a dedicated test Supabase project:

```env
API_URL=<test-supabase-project-url>
API_KEY_s=<test-supabase-service-role-key>
SUPER_ADMIN_EMAIL=<bootstrap-superadmin-email>
SUPER_ADMIN_PASSWORD=<bootstrap-superadmin-password>
TEST_USER1_EMAIL=<test-user1-email>
TEST_USER2_EMAIL=<test-user2-email>
TEST_USER3_EMAIL=<test-user3-email>
TEST_ADMIN_EMAIL=<test-admin-email>
TEST_PASSWORD=<password-for-test-accounts>  # defaults to TestPassword123!
```

The `reset_and_seed` fixture (session-scoped, autouse) resets the DB via `POST /admin/reset`, creates fresh test accounts, and provides JWT tokens and user IDs to all integration tests. Unit tests run normally without `test.env` — the fixture detects missing env vars and returns an empty dict.

### Docker

```bash
# Build
docker build -t apitest .

# Run (pass env vars at runtime — never bake .env into the image)
docker run -p 8000:8000 --env-file .env apitest
```

Cloud Run sets the `PORT` env var automatically; the server reads it on startup.

## Architecture

This is a FastAPI server that pulls data from a Supabase backend and exposes it via REST endpoints.

**Startup flow (`main.py`):** Starts the uvicorn server. On startup, `api.py`'s lifespan creates the async Supabase client (`init_client()`) and stores it on `app.state.supabase`, plus stores the underlying `httpx.AsyncClient` on `app.state.http_client`. Then initialises `FastAPICache` with an `InMemoryBackend`. On shutdown, the lifespan calls `await app.state.http_client.aclose()` to cleanly close HTTP connections. `GET /users/top` and `GET /matches` are cached for 60 seconds (namespaces `leaderboard` and `matches` respectively); both caches are cleared immediately when a match is confirmed. `GET /options` is cached for 60 seconds (namespace `options`).

**Module responsibilities:**
- `initialize.py` — creates both a sync Supabase client (`client`, at import time) and an async client (via `init_client()` during lifespan startup) from env vars (`API_URL`, `API_KEY_s`). `init_client()` injects its own `httpx.AsyncClient` via `AsyncClientOptions` and returns a `(AsyncClient, httpx.AsyncClient)` tuple so the lifespan owns the HTTP session lifecycle. The sync `client` is used only by `seed_data.py`. Also exports `get_supabase(request)`, a FastAPI dependency that returns `request.app.state.supabase` — all endpoints use `Depends(get_supabase)` to receive the async client. Also exports `create_client()` for standalone scripts
- `helpers.py` — shared auth utilities (`resolve_token`, `resolve_user_profile`, `ROLE_MAP`) and the `DELETED_USER_SENTINEL_ID` constant (UUID of the `[deleted]` sentinel profile used for match history preservation); all functions are `async` and accept an `AsyncClient` parameter (injected by callers via `Depends(get_supabase)`); `resolve_token` validates that the `Authorization` header starts with exactly `"Bearer "` and returns 401 immediately if not; contains **functions only, no endpoints** — import from here wherever JWT resolution or role checks are needed
- `rate_limit.py` — `slowapi` rate limiter instance (`limiter`) and key function (`_user_id_key`: per-user-ID bucket, falls back to IP); imported by `api.py` and `matches.py`
- `models.py` — all Pydantic response models (`RootResponse`, `HealthResponse`, `UserSummary`, `LeaderboardEntry`, `RuleSetOption`, `OptionsResponse`, `UpdatePreferencesRequest`, `PreferencesResponse`, `ChangeUsernameRequest`, `ChangeUsernameResponse`, `ChangeEmailRequest`, `PublicUserProfile`, `PublicUserProfileResponse`, `AuthUserInfo`, `RecentMatch`, `FullMatch`, `PendingMatch`, `BulkMatchActionRequest`, `BulkMatchResult`, `BulkMatchActionResponse`, and the envelope types `UsersListResponse`, `LeaderboardResponse`, `AuthUserResponse`, `UserMatchesResponse`, `UnconfirmedMatchesResponse`, `UserMatchHistoryResponse`, `RecentMatchesResponse`, `MatchResponse`, `PendingMatchesResponse`); imported by `api.py`, `users.py`, `matches.py`, and `admin.py`
- `api.py` — FastAPI app setup: CORS middleware (including `PATCH` method), `SlowAPIMiddleware` (rate limiting), router mounting, public endpoints (`/`, `/health`, `/version`, `/options`), and lifespan (creates async Supabase client + httpx client, initialises `FastAPICache` with `InMemoryBackend`, closes httpx client on shutdown)
- `users.py` — all `/users/*` endpoints (`GET /users`, `GET /users/top`, `GET /users/me`, `GET /users/me/matches`, `GET /users/me/matches/unconfirmed`, `PATCH /users/me/preferences`, `PATCH /users/me/username`, `PATCH /users/me/email`, `DELETE /users/me`, `PATCH /users/{user_id}/preferences`, `PATCH /users/{user_id}/username`, `PATCH /users/{user_id}/email`, `DELETE /users/{user_id}`, `GET /users/{user_id}`, `GET /users/{user_id}/matches`), all `async def`, mounted onto `app` via `APIRouter(prefix="/users")`; includes private helpers `_validate_option` (validates a value against a lookup table), `_apply_preferences` (validates + writes all four preference fields), and `_is_valid_email` (regex-based email format check); account deletion relies on the `before_profile_delete` DB trigger to reassign match FK columns to the sentinel UUID
- `matches.py` — match endpoints (`GET /matches`, `POST /matches`, `POST /matches/confirm`, `POST /matches/reject`), all `async def`, mounted onto `app` via `APIRouter`; `report_match` calls the `report_match` Postgres RPC which atomically fetches profiles, calculates ELO, and inserts the match row (eliminates TOCTOU race); `confirm_matches` batch-fetches all requested matches in one round trip then calls `confirm_match_and_update_elo` once per match, clearing `leaderboard` and `matches` caches once after the loop if any succeeded; `reject_matches` follows the same batch pattern using the `reject_match` Postgres RPC; both bulk endpoints accept 1–50 match IDs (`BulkMatchActionRequest`), apply per-match authorization with partial-success semantics, and return `BulkMatchActionResponse`; all three write endpoints are rate-limited via `@limiter.limit()`
- `admin.py` — admin/superAdmin routes, `async def`, mounted onto `app` via `APIRouter`; `_require_super_admin` enforces `role_id >= 3`; `_require_admin` enforces `role_id >= 2`; `GET /admin/matches/pending` returns paginated system-wide pending matches (admin+superAdmin); `POST /admin/reset` deletes all matches and non-bootstrap auth users, excluding the `[deleted]` sentinel (test-infrastructure only); seed logic itself is sync via `seed_data.py`
- `seed_data.py` — test data creation logic (`create_test_users`, `create_test_matches`); used by `admin.py` and runnable standalone via CLI subcommands; ELO formula is inlined (no `elo.py` dependency)
- `main.py` — starts the uvicorn server
- `Dockerfile` — production image using `python:3.10-slim` + uv; deps installed in a cached layer before source copy
- `.dockerignore` — excludes `.env`, `__pycache__`, `.git`, `.venv`, markdown docs, and `plans/` from the build context
- `tests/conftest.py` — shared pytest fixtures: session-scoped `app_client` (ASGI transport), `reset_and_seed` (autouse, resets DB + creates test accounts), `sync_supabase` (service-role Supabase sync client for direct DB manipulation in tests), token fixtures (`user1_token` through `super_admin_token`), ID fixtures, and helper functions (`_bearer`, `_decode_jwt_sub`)
- `tests/test_helpers.py` — unit tests for `helpers.py` (auth resolution logic)
- `tests/test_rate_limit.py` — unit tests for `rate_limit.py` (rate limiter key function)
- `tests/test_public.py` — integration tests for public endpoints (`/`, `/health`, `/options`, `/users/top`, `/matches`, `/matches/{match_id}`, `/users/{user_id}`, `/users/{user_id}/matches`)
- `tests/test_users.py` — integration tests for authenticated `/users/*` endpoints (`GET /users`, `GET /users/me`, `GET /users/me/matches`, `GET /users/me/matches/unconfirmed`, `PATCH /users/me/preferences`, `PATCH /users/{user_id}/preferences`)
- `tests/test_account.py` — integration tests for account management endpoints (username change, email change, account deletion); uses sacrificial users for destructive delete tests to avoid breaking session-scoped fixtures

**API endpoints:**
- `GET /` — hello message
- `GET /health` — health check; verifies both the server and Supabase DB connection are alive; returns `{"status": "ok", "db": "ok"}` with HTTP 200 on success, or HTTP 503 with `{"status": "error", "db": "unreachable"}` if DB is unreachable
- `GET /version` — returns the API version from `pyproject.toml` (public, no auth required; returns `{"version": "<semver>"}`)
- `GET /options` — returns valid values for all four preference fields (genders, games, weapons, shields) from lookup tables, plus `rule_sets` as `[{id, name}]` objects from the `rule_sets` table (public, no auth required; cached 60 s, namespace `options`)
- `GET /users/top` — leaderboard: top 100 players by ELO (public, no auth required; only includes users with a non-NULL username; cached 60 s, namespace `leaderboard`; cleared on match confirmation)
- `GET /matches` — recent confirmed matches (public, no auth required; sorted by `confirmedAt` DESC, max 100; cached 60 s, namespace `matches`; cleared on match confirmation)
- `GET /matches/{match_id}` — full details for a single match by ID (public, no auth required; returns any match state: pending, confirmed, or rejected; 404 if not found)
- `GET /users/{user_id}` — public profile for any player: id, username, elo, wins, losses, and optional preference fields (public, no auth required; 404 if user not found)
- `GET /users/{user_id}/matches` — confirmed match history for any player (public, no auth required; cursor-based pagination via `limit` and `before` query params; returns `matches` + `next_cursor`; 404 if user not found)
- `GET /users` — list all users except the authenticated user (id + username only); only includes users with a non-NULL username; for populating match-reporting dropdowns (requires `Authorization` header)
- `GET /users/me` — look up the authenticated user (requires `Authorization` header)
- `GET /users/me/matches` — all non-rejected matches for the authenticated user, split into `confirmed` (sorted by `confirmedAt` DESC, max 100) and `unconfirmed` (sorted by `reportedAt` DESC, max 100) lists (requires `Authorization` header)
- `GET /users/me/matches/unconfirmed` — unconfirmed, non-rejected matches for the authenticated user (requires `Authorization` header)
- `PATCH /users/me/preferences` — update the caller's own preference fields (gender, preferredGame, preferredWeapon, preferredShield); values validated against lookup tables; returns 422 for invalid values (requires `Authorization` header)
- `PATCH /users/me/username` — change the authenticated user's username; username must be 3–24 characters matching `[a-zA-Z0-9_-]`; 409 if taken (requires `Authorization` header)
- `PATCH /users/me/email` — request email change; sends Supabase confirmation email to new address; 422 for invalid email (requires `Authorization` header)
- `DELETE /users/me` — delete own account; match history preserved via `before_profile_delete` DB trigger (reassigns match FKs to sentinel); returns `{"deleted": "<user_id>"}` (requires `Authorization` header)
- `PATCH /users/{user_id}/preferences` — update any user's preferences; superAdmin only (`role_id >= 3`); returns 403 for non-superAdmins, 404 if target user not found (requires `Authorization` header)
- `PATCH /users/{user_id}/username` — superAdmin changes any user's username; 403 for non-superAdmins, 404 if user not found, 409 if taken (requires `Authorization` header)
- `PATCH /users/{user_id}/email` — superAdmin changes any user's email (immediate, no confirmation); 403 for non-superAdmins, 422 for invalid email (requires `Authorization` header)
- `DELETE /users/{user_id}` — superAdmin deletes any account; 400 if target is sentinel or bootstrap superAdmin; 403 for non-superAdmins, 404 if not found (requires `Authorization` header)
- `POST /matches` — report a match (body: `{winner_id, loser_id, rule_set_id}`; `rule_set_id` is a required UUID referencing the `rule_sets` table; requires `Authorization` header; regular users must be a participant, admins/superAdmins can report for any two users; ELO snapshot and delta are calculated atomically in the `report_match` Postgres RPC; returns 422 if `rule_set_id` is invalid)
- `POST /matches/confirm` — confirm 1–50 pending matches (body: `{"match_ids": [...]}`); per-match authorization + partial-success semantics; calls `confirm_match_and_update_elo` Postgres RPC per match; clears `leaderboard`/`matches` caches once if any succeeded; returns `BulkMatchActionResponse` (requires `Authorization` header)
- `POST /matches/reject` — reject 1–50 pending matches (body: `{"match_ids": [...]}`); per-match authorization + partial-success semantics; calls `reject_match` Postgres RPC per match; no ELO effect; returns `BulkMatchActionResponse` (requires `Authorization` header)
- `GET /admin/matches/pending` — all pending (confirmedAt IS NULL, rejectedAt IS NULL) matches system-wide; admin or superAdmin only; cursor-based pagination via `limit` (default 50, max 100) and `before` (ISO 8601 `reportedAt` cursor); sorted by `reportedAt` DESC (requires `Authorization` header)
- `POST /admin/reset` — delete all `Matches` rows and all auth users except the bootstrap superAdmin (identified by `SUPER_ADMIN_EMAIL` env var) and the `[deleted]` sentinel user; superAdmin only; test-infrastructure only — never call in production (requires `Authorization` header)
- `POST /admin/seed/users` — create test users; superAdmin only (requires `Authorization` header)
- `POST /admin/seed/matches` — create test matches; superAdmin only (requires `Authorization` header)
- `DELETE /admin/matches/{match_id}` — permanently delete a match by ID; superAdmin only (requires `Authorization` header)

## Authentication

**JWTs must be passed in the `Authorization` header — never in URLs or request bodies.**

```
Authorization: Bearer <supabase_jwt>
```

JWTs in URLs appear in server logs, proxy logs, and browser history. JWTs in request bodies risk appearing in application logs. The header is the only safe transport.

**Resolving a token + profile** — use `resolve_user_profile` from `helpers.py` for any endpoint that needs the caller's identity and role. It combines JWT resolution with a profile lookup and raises 401/404 automatically. The async Supabase client is injected via `Depends(get_supabase)` and passed to the helper:

```python
from fastapi import Depends, Header
from supabase import AsyncClient
from initialize import get_supabase
from helpers import resolve_user_profile, ROLE_MAP

async def my_endpoint(authorization: str = Header(...), supabase: AsyncClient = Depends(get_supabase)):
    caller = await resolve_user_profile(authorization, supabase)
    # caller = { "user_id": str, "role_id": int, "username": str }
```

For endpoints that only need the raw JWT user (no profile/role), use `resolve_token` directly:

```python
from fastapi import Depends, Header
from supabase import AsyncClient
from initialize import get_supabase
from helpers import resolve_token

async def my_endpoint(authorization: str = Header(...), supabase: AsyncClient = Depends(get_supabase)):
    user = await resolve_token(authorization, supabase)  # raises 401 on invalid/expired token
    user_id = user.user.id
```

**Checking superAdmin** — use `_require_super_admin` from `admin.py` as a reference pattern. It accepts `authorization` and `supabase` params, calls `resolve_user_profile`, and checks `role_id` against `ROLE_MAP["superAdmin"]` (= 3).

**Report match authorization** (`POST /matches`):
- `role_id >= ROLE_MAP["admin"]` — can report a match for any two users
- `role_id == ROLE_MAP["user"]` — can only report if they are `winner_id` or `loser_id`

**Confirm match authorization** (`POST /matches/confirm`, applied per match ID):
- `role_id >= ROLE_MAP["admin"]` — can confirm any unconfirmed, unrejected match
- `role_id == ROLE_MAP["user"]` — can confirm only if they are a participant (`winnerId` or `loserId`) and are NOT the `reporterId`

**Reject match authorization** (`POST /matches/reject`, applied per match ID):
- `role_id >= ROLE_MAP["admin"]` — can reject any unconfirmed, unrejected match
- `role_id == ROLE_MAP["user"]` — can reject if they are a participant (`winnerId` or `loserId`) OR the `reporterId`

**Update preferences authorization** (`PATCH /users/me/preferences`):
- Any authenticated user — updates their own profile only (user_id from JWT)

**Update user preferences authorization** (`PATCH /users/{user_id}/preferences`):
- `role_id >= ROLE_MAP["superAdmin"]` — can update any user's preferences
- All other roles — `403 Forbidden`

**Change username authorization** (`PATCH /users/me/username`):
- Any authenticated user — changes their own username (user_id from JWT)

**Change user username authorization** (`PATCH /users/{user_id}/username`):
- `role_id >= ROLE_MAP["superAdmin"]` — can change any user's username
- All other roles — `403 Forbidden`

**Change email authorization** (`PATCH /users/me/email`):
- Any authenticated user — triggers confirmation email to new address

**Change user email authorization** (`PATCH /users/{user_id}/email`):
- `role_id >= ROLE_MAP["superAdmin"]` — immediate email change, no confirmation
- All other roles — `403 Forbidden`

**Delete account authorization** (`DELETE /users/me`):
- Any authenticated user — deletes their own account (user_id from JWT)

**Delete user authorization** (`DELETE /users/{user_id}`):
- `role_id >= ROLE_MAP["superAdmin"]` — can delete any user except sentinel and bootstrap superAdmin
- All other roles — `403 Forbidden`

**Error responses:**
- `400` — invalid request (e.g. winner and loser are the same user, match already confirmed, match already rejected, or attempting to delete sentinel/bootstrap superAdmin)
- `401` — invalid or expired JWT, or `Authorization` header not in `Bearer <token>` format
- `403` — valid JWT but insufficient role
- `404` — valid JWT but no profile or match found
- `409` — conflict (e.g. username already taken)
- `422` — missing `Authorization` header (automatic from FastAPI), `match_id`/`user_id` path parameter is not a valid UUID, or invalid preference value not in lookup table
- `429` — rate limit exceeded (write endpoints only; 10/min for `POST /matches`, 20/min for confirm/reject, 5/min for account management: `DELETE /users/me`, `PATCH /users/me/username`, `PATCH /users/me/email`)

## Postgres Functions

The following Postgres functions exist in the database and are called via `client.rpc(...)`:

- **`report_match(p_winner_id, p_loser_id, p_reporter_id, p_reporter_name, p_reported_at, p_rule_set_id)`** — atomically fetches both player profiles with `FOR SHARE` locks, calculates the ELO delta, and inserts the `Matches` row (including `ruleSetId` FK) in a single transaction. Eliminates the TOCTOU race between the profile ELO snapshot and the match insert. Called by `report_match` in `matches.py`.
- **`confirm_match_and_update_elo(p_match_id, p_confirmed_at, p_confirmed_by_id, p_confirmed_by_name)`** — atomically confirms a match and applies the ELO delta to both players in a single transaction. Uses `SELECT ... FOR UPDATE` to prevent concurrent confirmations of the same match from causing a race condition. Called once per match ID by `confirm_matches` in `matches.py`.
- **`reject_match(p_match_id, p_rejected_at, p_rejected_by_id, p_rejected_by_name)`** — atomically rejects a match inside a transaction with `SELECT ... FOR UPDATE`. Raises a Postgres exception if the match is already confirmed (`P0003`) or already rejected (`P0004`), preventing the TOCTOU race where two concurrent reject requests could both overwrite audit fields. Called once per match ID by `reject_matches` in `matches.py`.
- **`get_user_matches(p_user_id)`** — returns a JSONB object with `confirmed` and `unconfirmed` arrays, each containing up to 100 matches for the given user (non-rejected; confirmed sorted by `confirmedAt DESC`, unconfirmed by `reportedAt DESC`). Replaces two sequential PostgREST queries with a single round trip. Called by `get_matches` in `users.py`.
- **`reassign_matches_on_profile_delete()`** — `SECURITY DEFINER` trigger function (`SET search_path = 'public'`) fired by the `before_profile_delete` trigger on `profiles`. First auto-rejects any pending matches where the deleted user is a participant (`winnerId` or `loserId`) by setting `rejectedAt`, `rejectedById`, and `rejectedByName` (to `[deleted]`). Then reassigns all match FK columns (`winnerId`, `loserId`, `reporterId`, `confirmedById`, `rejectedById`) from the deleted user to the `[deleted]` sentinel UUID. Fires automatically during GoTrue's CASCADE delete of `auth.users` → `profiles`. The `search_path` setting is critical: the GoTrue execution context uses an `auth`-only `search_path` — without `SET search_path = 'public'`, the `"Matches"` table reference fails.

When adding new DB-side logic, prefer Postgres functions over read-modify-write patterns in Python for any operation that must be atomic or involves multiple related rows.

## Database Migrations

**Always use the Supabase MCP (`apply_migration`) for schema changes — never raw SQL scripts, Python migration files, or direct DB connections.**

If the MCP call fails or returns an error:
1. Stop plan implementation immediately
2. Report the error to the user
3. Wait for explicit instructions before proceeding

Do not attempt workarounds (fallback scripts, manual SQL, etc.) without user approval.

## Documentation Policy

After every task (new endpoint, feature, config change, etc.), update all three documentation files:
1. **`CLAUDE.md`** — Keep architecture, module responsibilities, API endpoints, and commands sections current
2. **`FRONTEND_API.md`** — Keep frontend-facing API reference in sync (endpoints, request/response formats, error codes)
3. **`README.md`** — Keep setup instructions, endpoint tables, and feature list up to date

Do not consider a task complete until all three files are updated.

After implementing a plan from the `plans/` directory, move the plan file to `plans/completed/` rather than deleting it.

## Bugs

The `bugs/` directory tracks known issues and their resolution:

- `bugs/code-review.md` — open issues identified in code review; each entry stays here until fixed
- `bugs/fixed-bugs.md` — resolved issues; when a fix is applied, remove the issue's section from `code-review.md` and append it to `fixed-bugs.md` with a note describing the fix

When fixing an issue from `bugs/code-review.md`:
1. Remove its full section (heading + body) from `code-review.md` and update the summary table at the bottom to remove that row
2. Append the section to `fixed-bugs.md` with a `**Fix:**` line summarizing what was changed and which commit/PR resolved it

## CORS

CORS is configured in `api.py` via `CORSMiddleware`. Auth uses JWTs in the `Authorization` header (not cookies), so `allow_credentials` is `False`.

Allowed origins:
- `http://localhost:8081` — Metro bundler
- `http://localhost:19006` — Expo web
- `http://localhost:8080` — alternative dev port
- Additional origins from `CORS_ORIGINS` env var (comma-separated) — use this for Cloud Run URLs, production web domains, etc.

Android and iOS native apps bypass CORS entirely (no browser enforcement). Only React Native Web (browser) is affected.

**When adding a production domain:** set `CORS_ORIGINS=https://your-domain.com` in the environment (`.env` locally, Cloud Run env vars in production). No code change required.

## Environment Variables

A `.env` file is required (gitignored). Missing required vars will raise `KeyError` at startup.

Required:
- `API_URL` — Supabase project URL
- `API_KEY_s` — Supabase service role key (used for client creation)

Optional:
- `HOST` — server bind address (default `0.0.0.0`)
- `PORT` — server port (default `8000`)
- `CORS_ORIGINS` — comma-separated list of additional allowed CORS origins (e.g. Cloud Run URL, production web domain)
- `TEST_PASSWORD` — password assigned to all accounts created by `seed_data.py`; used when calling `create_test_users` (via `POST /admin/seed/users` or the CLI); defaults to `"TestPassword123!"` if unset; not needed in production
- `SUPER_ADMIN_EMAIL` — email of the bootstrap superAdmin account to skip when `POST /admin/reset` deletes auth users; required only when calling that endpoint (comes from `test.env` in test runs)