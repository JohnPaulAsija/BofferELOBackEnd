# scripts/

CLI tools that operate on the database. Currently houses `seed_demo.py`. Future operational scripts (data migrations, one-shot maintenance, etc.) belong here too.

> The repo also has `seed_data.py` at the root. That file is **separate** — it provides test-infrastructure fixtures imported by `admin.py` and pytest, and is not invoked manually. It stays at the root because moving it would touch working code for no functional gain.

## `seed_demo.py` — Demo data

Populates the DB with ~174 themed users (Arthurian, LoTR, Biblical, etc.) and ~1,740 back-dated matches so the leaderboard, `/matches`, and per-user profile pages look organically used. Designed for safe re-runs against prod after verification in test.

### Subcommands

| Command | What it does |
|---|---|
| `users` | Create demo users only (no matches) |
| `matches` | Add demo matches in a date window (auto-detects gap since last run) |
| `all` | Reset existing demo data, then create users + full match history |
| `reset` | Delete all `@demo.boffer.local` users and their matches |
| `verify` | Read-only — print counts, ELO spread, gender split, state distribution |

### Recommended workflow

**Run all commands from the project root** (the directory containing `pyproject.toml`), not from `scripts/`. Python's `-m` flag resolves `scripts.seed_demo` as a package by looking at the current directory; running from inside `scripts/` will fail with `ModuleNotFoundError: No module named 'scripts'`.

**Always run against the test database first.** The `--env-file` flag selects which `.env` to load:

```bash
# Against test DB (test.env)
uv run python -m scripts.seed_demo all    --env-file test.env
uv run python -m scripts.seed_demo verify --env-file test.env

# After confirming verify output, run against prod
uv run python -m scripts.seed_demo all
# (defaults to .env; non-test hosts require typing PROD to confirm)
```

The `--env-file` flag must come AFTER the subcommand (e.g. `all --env-file test.env`, not `--env-file test.env all`). Placing it before the subcommand will error out — this is intentional, since the alternative argparse pattern silently overrode the value with the default and risked targeting prod by mistake.

### Top-up workflow (weeks later)

```bash
uv run python -m scripts.seed_demo matches --env-file test.env
uv run python -m scripts.seed_demo matches            # then prod
```

No flags needed. The script auto-resolves `--from` to the latest existing demo match's `reportedAt`, defaults `--to` to today, and computes a count proportional to the window (≈10 matches per user per 60-day window). Override any of these explicitly with `--from DATE` / `--to DATE` / `--count N`.

### Theme selection

```bash
# Just one theme
uv run python -m scripts.seed_demo all --themes arthurian --env-file test.env

# Multiple themes
uv run python -m scripts.seed_demo all --themes arthurian,robinhood,women --env-file test.env

# All themes (default — same as no --themes flag)
uv run python -m scripts.seed_demo all --env-file test.env
```

### Safety

Three protections against accidentally hitting prod:

1. **Target host printed at startup** — script reports the `API_URL` so you can confirm before any writes.
2. **`PROD` token prompt for non-test hosts** — `y/n` is fat-finger territory. Production prompts require typing the literal token `PROD` (uppercase). Detection keys on the `--env-file` path containing `test`, since Supabase project hostnames are auto-generated IDs.
3. **Strict cleanup filter** — `reset` only touches users whose email ends in `@demo.boffer.local`. Hard cap on the deletion count (matches the catalog size) aborts if something unexpected expands the demo pool.

The `--yes` flag bypasses prompts. Intended for scripting/CI only. Don't use interactively against prod.

### Common pitfalls

- **`Empty rule_sets` error:** the seeder picks `preferredGame` from the `rule_sets` table. Apply the `rule_sets` migration first if you're seeding a fresh DB.
- **`Duplicate email` error mid-run:** a previous run left demo users without cleaning up. Run `reset` first, then re-run.
- **Verify shows fewer users than expected:** the `all` workflow resets first; if reset fails mid-way some users may remain. Run `reset` again, then `users`/`matches` individually.
- **Pending matches sitting around:** ~7% of generated matches stay pending intentionally, and only within the last 5 days of the window. If you don't want any pending matches in prod, pass `--count` higher than the default to dilute them or accept the small number.

### Design

Full design at [plans/2026-05-13-demo-seed-design.md](../plans/2026-05-13-demo-seed-design.md).
