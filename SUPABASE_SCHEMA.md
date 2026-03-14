# Supabase Schema & Configuration

Reference for the Supabase project backing the ELO ranking API.

---

## Project Details

| Property       | Value                                      |
|----------------|--------------------------------------------|
| **Name**       | `<your-project-name>`                      |
| **Project ID** | `<your-project-id>`                        |
| **API URL**    | `https://<your-project-id>.supabase.co`    |
| **Region**     | `<your-region>`                            |
| **Postgres**   | 17.6.1 (engine 17, release channel: GA)    |
| **Status**     | ACTIVE_HEALTHY                             |
| **Created**    | 2026-01-03                                 |

---

## Tables

### `public.roles`

Lookup table for user permission levels. Seeded with three fixed rows — do not insert or delete rows without updating `ROLE_MAP` in `helpers.py`.

| Column | Type     | Constraints                      | Notes                   |
|--------|----------|----------------------------------|-------------------------|
| `id`   | `bigint` | PK, `GENERATED ALWAYS AS IDENTITY`, unique | Auto-incremented        |
| `name` | `text`   | NOT NULL, unique                 | e.g. `user`, `admin`, `superAdmin` |

**Seed data:**

| id | name         |
|----|--------------|
| 1  | `user`       |
| 2  | `admin`      |
| 3  | `superAdmin` |

---

### `public.profiles`

One row per registered user. Created automatically via the `on_auth_user_created` trigger when a new row is inserted into `auth.users`.

| Column     | Type     | Constraints                             | Default     | Notes                                  |
|------------|----------|-----------------------------------------|-------------|----------------------------------------|
| `id`       | `uuid`   | PK, FK → `auth.users.id`               |             | Matches the Supabase Auth user ID      |
| `email`    | `text`   | NOT NULL, unique                        | `''`        | Copied from `auth.users` at signup     |
| `username` | `text`   | nullable, unique, `char_length >= 3`   |             | Set from `raw_user_meta_data->>'username'` at signup by trigger |
| `elo`      | `bigint` | nullable                                |             | Current ELO rating; starts at `1000` (set by seed or first match) |
| `wins`     | `bigint` | nullable                                |             | Total confirmed wins                   |
| `losses`   | `bigint` | nullable                                |             | Total confirmed losses                 |
| `role_id`  | `bigint` | NOT NULL, FK → `public.roles.id`       | `1`         | Defaults to `user` role                |
| `termsAcceptedAt` | `timestamptz` | nullable                      |             | Set to `NOW()` at signup by the `handle_new_user` trigger; non-NULL for all users |
| `gender`          | `text`        | nullable                      |             | Must match a value in `gender_options` |
| `preferredGame`   | `text`        | nullable                      |             | Must match a value in `game_types`     |
| `preferredWeapon` | `text`        | nullable                      |             | Must match a value in `weapon_types`   |
| `preferredShield` | `text`        | nullable                      |             | Must match a value in `shield_types`   |

**Indexes:** `profiles_pkey` (id), `profiles_email_key` (email), `profiles_username_key` (username)

---

### Lookup Tables: `gender_options`, `game_types`, `weapon_types`, `shield_types`

Four single-column lookup tables storing valid option values for user preferences. Each has `name TEXT PRIMARY KEY`. Adding/removing options requires only an INSERT/DELETE — no migration or code change.

| Table            | Initial Values |
|------------------|----------------|
| `gender_options` | Male, Female, Other |
| `game_types`     | Hearthlight, Dagorhir |
| `weapon_types`   | One Handed Sword, Two Handed Sword, One Handed Spear, Two Handed Spear, Bow, Javelin |
| `shield_types`   | None, Back, Hand (grip), Hand (strap), Arm, Shoulder |

---

### `public.Matches`

One row per reported match. A match is **pending** when `confirmedAt IS NULL` and **confirmed** when `confirmedAt IS NOT NULL`. Rejected matches have `rejectedAt IS NOT NULL` and have no ELO effect. The ELO delta is pre-calculated at report time and stored in `eloChange`; it is applied atomically at confirmation via the `confirm_match_and_update_elo` RPC.

| Column            | Type           | Constraints         | Default                        | Notes                                            |
|-------------------|----------------|---------------------|--------------------------------|--------------------------------------------------|
| `id`              | `uuid`         | PK                  | `uuid_generate_v4()`           | Auto-generated match ID                          |
| `winnerId`        | `uuid`         | nullable            |                                | References `profiles.id` (no FK constraint)      |
| `winnerName`      | `varchar`      | nullable            |                                | Snapshot of username at report time              |
| `loserId`         | `uuid`         | nullable            |                                | References `profiles.id` (no FK constraint)      |
| `loserName`       | `varchar`      | nullable            |                                | Snapshot of username at report time              |
| `winnerEloBefore` | `bigint`       | nullable            |                                | Winner's ELO at report time                      |
| `loserEloBefore`  | `bigint`       | nullable            |                                | Loser's ELO at report time                       |
| `eloChange`       | `bigint`       | nullable            |                                | Delta applied to winner (+) and loser (-) on confirm |
| `reporterId`      | `uuid`         | nullable            |                                | User who reported the match                      |
| `reporterName`    | `varchar`      | nullable            |                                | Snapshot of reporter's username at report time   |
| `reportedAt`      | `timestamptz`  | NOT NULL            | `now() AT TIME ZONE 'utc'`     | When the match was reported                      |
| `confirmedById`   | `uuid`         | nullable            |                                | User who confirmed the match                     |
| `confirmedByName` | `varchar`      | nullable            |                                | Snapshot of confirmer's username                 |
| `confirmedAt`     | `timestamptz`  | nullable            |                                | NULL = pending; non-NULL = confirmed             |
| `rejectedById`    | `uuid`         | nullable            |                                | User who rejected the match                      |
| `rejectedByName`  | `text`         | nullable            |                                | Snapshot of rejecter's username                  |
| `rejectedAt`      | `timestamptz`  | nullable            |                                | NULL = not rejected; non-NULL = rejected         |

**Indexes:** `matches_pkey` (id)

> **Note:** `winnerId`, `loserId`, `reporterId`, `confirmedById`, and `rejectedById` store UUIDs that reference `profiles.id` and `auth.users.id`, but no foreign key constraints are enforced at the DB level. Name snapshot columns (`winnerName`, `loserName`, etc.) are denormalized copies recorded at the time of the action.

---

## Row Level Security (RLS)

RLS is **enabled** on all three public tables. The backend uses the **service role key**, which bypasses RLS entirely. The policies below apply to direct PostgREST access (e.g. from a frontend using the anon or authenticated key).

### `public.profiles`

| Policy Name                              | Command  | Roles    | Condition                        |
|------------------------------------------|----------|----------|----------------------------------|
| Public profiles are viewable by everyone | `SELECT` | `public` | `true` (all rows visible)        |
| Users can insert their own profile       | `INSERT` | `public` | `auth.uid() = id`                |
| Users can update own profile             | `UPDATE` | `public` | `auth.uid() = id`                |

### `public.roles`

| Policy Name               | Command  | Roles           | Condition                 |
|---------------------------|----------|-----------------|---------------------------|
| roles_read_authenticated  | `SELECT` | `authenticated` | `true` (all rows visible) |

### `public.Matches`

RLS is enabled but **no policies are defined**. All access from the anon/authenticated role via PostgREST is blocked by default. The backend accesses this table exclusively through the service role key, which bypasses RLS.

---

## Postgres Functions

### `report_match`

```sql
report_match(
  p_winner_id     uuid,
  p_loser_id      uuid,
  p_reporter_id   uuid,
  p_reporter_name text,
  p_reported_at   timestamptz
) RETURNS SETOF "Matches"
```

Atomically fetches both player profiles with `FOR SHARE` locks, calculates the ELO delta, and inserts the `Matches` row — all in a single transaction. The `FOR SHARE` lock prevents any concurrent `confirm_match_and_update_elo` from mutating either player's ELO between the read and the insert (eliminates the TOCTOU race condition).

**Steps:**
1. Lock winner profile row (`SELECT ... FOR SHARE`); raise `P0001` (`winner_not_found`) if missing
2. Lock loser profile row (`SELECT ... FOR SHARE`); raise `P0001` (`loser_not_found`) if missing
3. Calculate ELO delta: `max(1, round(32 * (1 - 1 / (1 + 10^((loser_elo - winner_elo) / 400)))))`
4. Insert the `Matches` row with the locked ELO snapshot and calculated delta
5. Return the inserted match row

**Called by:** `POST /matches` in `matches.py`

---

### `confirm_match_and_update_elo`

```sql
confirm_match_and_update_elo(
  p_match_id        uuid,
  p_confirmed_at    timestamptz,
  p_confirmed_by_id uuid,
  p_confirmed_by_name text
) RETURNS json
```

Atomically confirms a match and applies the pre-calculated ELO delta to both players in a single transaction. Uses `SELECT ... FOR UPDATE` to prevent race conditions from concurrent confirmations.

**Steps:**
1. Lock the match row (`SELECT ... FOR UPDATE`)
2. Set `confirmedAt`, `confirmedById`, `confirmedByName` on the match
3. Add `eloChange` to winner's `elo`; increment winner's `wins` by 1
4. Subtract `eloChange` from loser's `elo`; increment loser's `losses` by 1
5. Return the updated match row as JSON

**Called by:** `POST /matches/confirm` in `matches.py` (once per match ID in the batch)

---

### `reject_match`

```sql
reject_match(
  p_match_id          uuid,
  p_rejected_at       timestamptz,
  p_rejected_by_id    uuid,
  p_rejected_by_name  text
) RETURNS SETOF "Matches"
```

Atomically rejects a match. Uses `SELECT ... FOR UPDATE` to prevent concurrent operations on the same row. Raises structured exceptions for invalid state transitions.

**Steps:**
1. Lock the match row (`SELECT ... FOR UPDATE`)
2. Raise `P0002` (`match_not_found`) if no row found
3. Raise `P0003` (`match_already_confirmed`) if `confirmedAt IS NOT NULL`
4. Raise `P0004` (`match_already_rejected`) if `rejectedAt IS NOT NULL`
5. Set `rejectedAt`, `rejectedById`, `rejectedByName` on the match
6. Return the updated match row

**Called by:** `POST /matches/reject` in `matches.py` (once per match ID in the batch)

---

### `get_user_matches`

```sql
get_user_matches(
  p_user_id  uuid
) RETURNS JSONB
```

Returns a JSONB object with two arrays — `confirmed` and `unconfirmed` — each containing up to 100 non-rejected matches for the given user, fetched in a single database call.

**Steps:**
1. Select the 100 most recent confirmed, non-rejected matches (`confirmedAt IS NOT NULL`, `rejectedAt IS NULL`) for the user, ordered by `confirmedAt DESC`; aggregate into a JSONB array
2. Select the 100 most recent unconfirmed, non-rejected matches (`confirmedAt IS NULL`, `rejectedAt IS NULL`) for the user, ordered by `reportedAt DESC`; aggregate into a JSONB array
3. Return `{"confirmed": [...], "unconfirmed": [...]}`

**Called by:** `GET /users/me/matches` in `users.py`

---

### `reassign_matches_on_profile_delete`

```sql
reassign_matches_on_profile_delete() RETURNS trigger
```

The sole mechanism for reassigning match FK columns (`winnerId`, `loserId`, `reporterId`, `confirmedById`, `rejectedById`) from a deleted user to the `[deleted]` sentinel UUID (`00000000-0000-0000-0000-000000000002`). Preserves match history when a user account is deleted. Fires automatically via the `before_profile_delete` trigger during GoTrue's CASCADE delete of `auth.users` → `profiles`.

- **Security:** `SECURITY DEFINER` (runs as `postgres`), `SET search_path = 'public'`
- The `search_path` setting is required because this trigger fires during a GoTrue CASCADE delete, which runs in an `auth`-schema context that does not include `public` — without it, the `"Matches"` table reference fails with `relation "Matches" does not exist`
- Skips execution if the sentinel itself is being deleted (safety guard)

**Invoked by:** `before_profile_delete` trigger (see below)

---

### `handle_new_user`

```sql
handle_new_user() RETURNS trigger
```

Trigger function that automatically creates a fully-populated `profiles` row when a new user signs up via Supabase Auth.

**Steps:**
1. Insert a new row into `public.profiles` with `id` and `email` from `NEW` (the new `auth.users` row), plus `username`, `termsAcceptedAt` (set to `NOW()`), `elo` (1000), `wins` (0), `losses` (0), and preference fields (`gender`, `preferredGame`, `preferredWeapon`, `preferredShield`) sourced from `NEW.raw_user_meta_data`

**Invoked by:** `on_auth_user_created` trigger (see below)

---

## Triggers

### `on_auth_user_created`

| Property        | Value                               |
|-----------------|-------------------------------------|
| **Table**       | `auth.users`                        |
| **Event**       | `INSERT`                            |
| **Timing**      | `AFTER`                             |
| **Function**    | `handle_new_user()`                 |

Fires after every new Supabase Auth signup. Ensures every authenticated user has a corresponding `profiles` row before they make any API calls.

---

### `before_profile_delete`

| Property        | Value                                          |
|-----------------|------------------------------------------------|
| **Table**       | `public.profiles`                              |
| **Event**       | `DELETE`                                       |
| **Timing**      | `BEFORE`                                       |
| **Function**    | `reassign_matches_on_profile_delete()`         |

Fires before a `profiles` row is deleted (including via CASCADE from `auth.users`). Reassigns all match FK references from the deleted user to the `[deleted]` sentinel, preserving match history.

> **Note:** The function must use `SET search_path = 'public'` because when triggered via GoTrue's CASCADE delete of `auth.users`, the execution context has a `search_path` that does not include `public`, causing `"Matches"` table lookups to fail.

---

## Migrations

Applied in order via `apply_migration` (Supabase MCP). Never modify or delete existing migration records.

| Version           | Name                                  | Description                                                       |
|-------------------|---------------------------------------|-------------------------------------------------------------------|
| `20260222182553`  | `add_roles_table_and_fk`              | Creates `roles` table; adds `role_id` FK to `profiles`           |
| `20260222183428`  | `fix_handle_new_user_trigger`         | Fixes `handle_new_user` trigger to correctly copy email on signup |
| `20260224183056`  | `add_rejection_columns_to_matches`    | Adds `rejectedAt`, `rejectedById`, `rejectedByName` to `Matches` |
| `20260224195250`  | `add_confirm_match_rpc`               | Creates `confirm_match_and_update_elo` Postgres function          |
| `20260225194848`  | `add_reject_match_rpc`                | Creates `reject_match` Postgres function                          |
| —                 | `add_terms_accepted_at_to_profiles`   | Adds `termsAcceptedAt` column to `profiles`                       |
| —                 | `add_lookup_tables_and_preference_columns` | Creates lookup tables (`gender_options`, `game_types`, `weapon_types`, `shield_types`); adds preference columns to `profiles` |
| —                 | `add_report_match_rpc`                | Creates `report_match(uuid, uuid, uuid, text, timestamptz)` function for atomic match reporting |
| —                 | `add_get_user_matches_rpc`            | Creates `get_user_matches(uuid)` function returning confirmed + unconfirmed arrays as JSONB      |
| —                 | `update_handle_new_user_populate_profile_at_signup` | Updates `handle_new_user` trigger to populate all profile fields (username, termsAcceptedAt, preferences, elo, wins, losses) from `raw_user_meta_data` at signup |
| —                 | `fix_reassign_matches_trigger_search_path` | Adds `SET search_path = 'public'` to `reassign_matches_on_profile_delete()` so it works when invoked from GoTrue's CASCADE delete context |
| —                 | `fix_delete_user_profile_search_path` | Adds `SET search_path = 'public'` to `delete_user_profile()` for the same reason |
| —                 | `drop_unused_delete_user_profile_function` | Drops the unused `delete_user_profile(uuid)` function; match reassignment is handled solely by the `before_profile_delete` trigger |

---

## Auth Configuration

Supabase Auth is used for identity. The backend **never issues or manages JWTs directly** — all tokens come from Supabase Auth and are validated by the Supabase client.

- **Token transport:** `Authorization: Bearer <jwt>` header only (never URLs or request bodies)
- **Client key used by backend:** service role key (`API_KEY_s` env var) — bypasses RLS
- **New user flow:** Supabase Auth signup → `on_auth_user_created` trigger → `profiles` row created automatically

---

## Extensions

The `uuid-ossp` extension (`extensions.uuid_generate_v4()`) is used to generate default UUIDs for `Matches.id`.
