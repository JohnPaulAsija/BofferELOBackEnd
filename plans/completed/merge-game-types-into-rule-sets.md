# Plan: Merge game_types into rule_sets

Games and rule sets are 1:1 equivalent with no variants. Unify them into `rule_sets` as the single source of truth. Remove the separate `game_types` table and `games` field from `/options`.

This is a **breaking API change** — bump the version from `0.1.0` → `0.2.0`.

---

## Steps

### 1. Migration (Supabase MCP `apply_migration`)
- Insert "Belegarth" into `rule_sets`
- Drop `game_types` table

### 2. `pyproject.toml`
- Bump version `0.1.0` → `0.2.0`

### 3. `models.py`
- Remove `games: list[str]` field from `OptionsResponse`

### 4. `api.py`
- Remove the `game_types` query from `GET /options`
- Remove `"games"` key from the returned dict

### 5. `users.py`
- In `_apply_preferences`, change `preferredGame` validation to query `rule_sets` on the `name` column instead of `game_types`

### 6. `seed_data.py`
- Replace the hardcoded `preferredGame` list with a dynamic fetch from `rule_sets` (same pattern already used for ruleset IDs)

### 7. Documentation
- **`CLAUDE.md`** — update architecture notes: remove `game_types` references, note that `rule_sets` now also drives the `preferredGame` preference; update `/options` endpoint description to remove `games`; update the lookup tables section
- **`FRONTEND_API.md`** — update `GET /options` response shape (remove `games`), add migration guide note for frontend consumers
- **`README.md`** — update feature list and endpoint table

---

## Frontend Update Instructions

Produce these instructions at the end of implementation for the frontend team:

### Breaking change in `GET /options` (v0.2.0)

**Removed:** `games` field from the `/options` response.

**Before:**
```json
{
  "games": ["Dagorhir", "Hearthlight"],
  "rule_sets": [
    { "id": "<uuid>", "name": "Dagorhir" },
    { "id": "<uuid>", "name": "Hearthlight" }
  ],
  ...
}
```

**After:**
```json
{
  "rule_sets": [
    { "id": "<uuid>", "name": "Dagorhir" },
    { "id": "<uuid>", "name": "Hearthlight" },
    { "id": "<uuid>", "name": "Belegarth" }
  ],
  ...
}
```

**Required frontend changes:**
1. **Preferred game dropdown** — replace any reference to `options.games` with `options.rule_sets.map(r => r.name)`
2. **Match reporting ruleset selector** — already uses `rule_sets`; no change needed beyond picking up the new Belegarth entry
3. **Stored `preferredGame` values** — the `preferredGame` field on user profiles still stores a plain string name (e.g. `"Belegarth"`); no UUID migration needed on the client side
