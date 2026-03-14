# Boffer Combat ELO API

Backend API for a React Native ELO ranking app for boffer combat games (Dagorhir, Belegarth, etc.) — medieval LARP combat sports using foam weapons.

## Features

- **ELO calculation** — compute and update player ratings after each confirmed match
- **Rankings** — sorted leaderboard of players by ELO (cached for 60 s)
- **Match history** — per-player match records
- **Rule sets** — matches are tagged with a rule set (e.g. Dagorhir, Hearthlight); available rule sets fetched from `/options`
- **Rate limiting** — write endpoints rate-limited per user (10/min for report, 20/min for confirm/reject; admins exempt)
- **Admin seeding** — create test users and matches (superAdmin only)

## Tech Stack

- **Python 3.10+** with [uv](https://docs.astral.sh/uv/) for dependency management
- **FastAPI** — REST API framework
- **Supabase** — database and auth backend
- **Uvicorn** — ASGI server

## Setup

### Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) installed

### Environment Variables

Create a `.env` file in the project root:

```env
API_URL=<your-supabase-project-url>
API_KEY_s=<your-supabase-service-role-key>

# Optional
HOST=0.0.0.0
PORT=8000
CORS_ORIGINS=https://my-service-xyz.run.app,https://my-app.com
TEST_PASSWORD=<password for seeded test accounts>  # defaults to TestPassword123! if unset
```

### Install & Run

```bash
# Install dependencies
uv sync

# Start the server
uv run python main.py
```

The server starts on `http://localhost:8000` by default.

## Docker

```bash
# Build
docker build -t apitest .

# Run (pass env vars at runtime)
docker run -p 8000:8000 --env-file .env apitest
```

The image uses a healthcheck against `GET /health`. Cloud Run sets the `PORT` env var automatically — the server reads it on startup, so no extra config is needed for Cloud Run deployments.

## API Endpoints

### Public

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Hello message |
| `GET` | `/health` | Health check — verifies server and DB connectivity; returns `{"status": "ok", "db": "ok"}` (200) or `{"status": "error", "db": "unreachable"}` (503) |
| `GET` | `/version` | API version — returns `{"version": "<semver>"}` from `pyproject.toml` |
| `GET` | `/users/top` | Leaderboard — top 100 players by ELO (cached 60 s) |
| `GET` | `/matches` | Recent confirmed matches (max 100, sorted by confirmed date, cached 60 s) |
| `GET` | `/matches/{match_id}` | Full details for a single match (any state: pending, confirmed, or rejected) |
| `GET` | `/options` | Valid values for preference fields (genders, games, weapons, shields) and rule sets (`[{id, name}]`); cached 60 s |
| `GET` | `/users/{user_id}` | Public profile for any player (id, username, elo, wins, losses, preferences) |
| `GET` | `/users/{user_id}/matches` | Confirmed match history for any player (cursor-paginated) |

### Authenticated (requires `Authorization: Bearer <jwt>`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/users` | List all users except self (id + username; for match-reporting dropdowns) |
| `GET` | `/users/me` | Get the authenticated user |
| `GET` | `/users/me/matches` | List all active matches (confirmed + unconfirmed, no rejected) for the authenticated user |
| `GET` | `/users/me/matches/unconfirmed` | List unconfirmed matches for the authenticated user |
| `PATCH` | `/users/me/preferences` | Update own preference fields (gender, game, weapon, shield) |
| `PATCH` | `/users/me/username` | Change own username |
| `PATCH` | `/users/me/email` | Request email change (confirmation email sent) |
| `DELETE` | `/users/me` | Delete own account (pending matches auto-rejected, confirmed history preserved) |
| `POST` | `/matches` | Report a match (requires `rule_set_id`; ELO delta pre-calculated at report time) |
| `POST` | `/matches/confirm` | Confirm one or more pending matches (up to 50); atomically applies ELO changes per match; partial success |
| `POST` | `/matches/reject` | Reject one or more pending matches (up to 50); no ELO effect; partial success |

### Admin

| Method | Path | Role required | Description |
|--------|------|--------------|-------------|
| `GET` | `/admin/matches/pending` | admin or superAdmin | List all pending (unconfirmed, unrejected) matches system-wide; cursor-paginated |
| `POST` | `/admin/reset` | superAdmin | Delete all matches and non-bootstrap auth users — test infrastructure only, never call in production |
| `POST` | `/admin/seed/users` | superAdmin | Create test users |
| `POST` | `/admin/seed/matches` | superAdmin | Create test matches |
| `PATCH` | `/users/{user_id}/preferences` | superAdmin | Update any user's preferences |
| `PATCH` | `/users/{user_id}/username` | superAdmin | Change any user's username |
| `PATCH` | `/users/{user_id}/email` | superAdmin | Change any user's email (immediate) |
| `DELETE` | `/users/{user_id}` | superAdmin | Delete any user account (pending matches auto-rejected) |
| `DELETE` | `/admin/matches/{match_id}` | superAdmin | Permanently delete a match by ID (no ELO rollback) |

## CORS

CORS is configured to allow specific origins only. Credentials mode is disabled — auth uses the `Authorization` header (JWT), not cookies.

**Allowed origins (dev):** `localhost:8081`, `localhost:19006`, `localhost:8080`

Additional origins (e.g. your Cloud Run URL or production web domain) are loaded from the `CORS_ORIGINS` env var — no code change needed:

```env
CORS_ORIGINS=https://my-service-xyz.run.app,https://my-app.com
```

Android and iOS native apps are unaffected by CORS.

## Authentication

All authenticated endpoints expect a Supabase JWT in the `Authorization` header:

```
Authorization: Bearer <supabase_jwt>
```

## Testing

```bash
# Run all tests (unit + integration)
uv run pytest

# Run only unit tests (no Supabase needed)
uv run pytest tests/test_helpers.py tests/test_rate_limit.py

# Run only integration tests (requires test.env)
uv run pytest tests/test_public.py tests/test_users.py
```

Integration tests require a `test.env` file with credentials for a dedicated test Supabase project. See `CLAUDE.md` for the full list of required variables.

The test suite uses pytest-asyncio with session-scoped fixtures. A `reset_and_seed` fixture automatically resets the DB and creates fresh test accounts once per session.

## Project Structure

```
├── main.py          # Uvicorn server entrypoint
├── api.py           # FastAPI app setup (CORS, router mounting, public endpoints)
├── users.py         # All /users/* endpoints (list, profile, matches)
├── matches.py       # Match endpoints (report, confirm, reject)
├── admin.py         # Admin-only routes (seed endpoints)
├── models.py        # Pydantic response models for all endpoints
├── helpers.py       # Shared auth utilities (resolve_token, resolve_user_profile, ROLE_MAP)
├── initialize.py    # Supabase client setup (sync + async) and get_supabase dependency
├── seed_data.py     # Test data creation (users + matches), CLI subcommands
├── Dockerfile       # Production container image
├── .dockerignore    # Docker build context exclusions
├── pyproject.toml   # Project metadata and dependencies
├── .env             # Environment variables (gitignored)
└── tests/
    ├── conftest.py      # Shared fixtures (app_client, reset_and_seed, tokens, IDs)
    ├── test_helpers.py  # Unit tests for auth helpers
    ├── test_rate_limit.py # Unit tests for rate limiter
    ├── test_public.py   # Integration tests for public endpoints
    ├── test_users.py    # Integration tests for authenticated /users/* endpoints
    └── test_account.py  # Integration tests for account management (username, email, delete)
```
