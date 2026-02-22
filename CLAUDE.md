# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

Backend API for a React Native ELO ranking app for **boffer combat games** (e.g. Dagorhir, Belegarth) — medieval LARP combat sports using foam weapons.

Core features:
- **ELO calculation** — compute and update player ratings after each confirmed match
- **Rankings** — return a sorted leaderboard of players by ELO
- **Match history** — show per-player match records

The Supabase `Matches` table is the primary data source. `confirmedAt` being NULL means a match is pending/unconfirmed; non-NULL means it is confirmed and should count toward ELO.

## Commands

This project uses `uv` for dependency management (Python 3.10).

```bash
# Install dependencies
uv sync

# Run the server
uv run python main.py

# Run the FastAPI app directly (without the startup data fetch)
uv run python api.py
```

## Architecture

This is a FastAPI server that pulls data from a Supabase backend and exposes it via REST endpoints.

**Startup flow (`main.py`):** On start, `poll_and_save_matches()` fetches all rows from the `Matches` Supabase table into a pandas DataFrame, then splits it into two module-level globals in `api.py`:
- `api.df_null` — rows where `confirmedAt` is NULL
- `api.df_confirmed` — rows where `confirmedAt` is non-NULL

The uvicorn server then starts and serves these cached DataFrames. The data is fetched once at startup and held in memory; there is no background polling after startup.

**Module responsibilities:**
- `initialize.py` — creates a Supabase client from env vars (`API_URL`, `API_KEY_s`)
- `api.py` — FastAPI app, route definitions, and `poll_and_save_matches()` data fetch logic
- `dbInteractions.py` — generic DB helpers (`fetch_data`, `retreive_user`); currently unused by `api.py`
- `main.py` — orchestrates startup: fetch → split DataFrames → start server

**API endpoints:**
- `GET /` — hello message
- `GET /health` — health check
- `GET /user/{jwt}` — look up a Supabase user by JWT
- `GET /data/Matches` — returns the two in-memory match DataFrames as JSON

## Environment Variables

A `.env` file is required (gitignored). The client in `initialize.py` reads:
- `API_URL` — Supabase project URL
- `API_KEY_s` — Supabase service role key (used for client creation)
- `API_KEY_p` — Supabase publishable/anon key (loaded but not currently used)
