"""Demo data seeder for the Boffer ELO backend.

Creates ~174 themed demo users and ~1,740 back-dated matches.
Designed for one-shot CLI use and incremental "top up" runs; not part of the API surface.

Run with --env-file test.env first to verify behavior, then against prod.
"""
import argparse
import os
import random
import sys
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from initialize import create_client
from helpers import ROLE_MAP


THEMES = {
    "arthurian": [
        "Arthur", "Lancelot", "Merlin", "Mordred",
        "Gawain", "Galahad", "Percival", "Tristan", "Kay",
        "Bedivere", "Lamorak", "Bors", "Dagonet", "Pellinore", "Geraint",
        "Yvain", "Accolon", "Balin",
    ],
    "robinhood": [
        "RobinHood", "LittleJohn", "WillScarlet", "FriarTuck",
        "Allan-a-Dale", "MuchMiller", "GuyOfGisbourne",
    ],
    "crusaders": [
        "RichardLionheart", "GodfreyBouillon", "Tancred", "Bohemond",
        "RaymondToulouse", "FrederickBarbarossa", "ReynaldChatillon",
        "ConradMontferrat", "BaldwinIV", "HughPayens", "WilliamMarshal",
        "RobertSable",
    ],
    "saracens": ["Saladin", "AlAdil", "NurAdDin", "Shirkuh"],
    "roland": [
        "Roland", "Oliver", "Ganelon", "Charlemagne", "Turpin",
        "Marsile", "Naimon", "Ogier", "Anseis", "Berenger",
    ],
    "beowulf": [
        "Beowulf", "Hrothgar", "Wiglaf", "Unferth",
        "Hygelac", "Ecgtheow", "Breca",
    ],
    "chaucer": [
        "Palamon", "Arcite", "Theseus",
        "Nicholas", "Absolon",
        "Chanticleer",
        "Walter",
    ],
    "eddas": [
        "Odin", "Thor", "Loki", "Freyr", "Tyr",
        "Heimdall", "Baldr", "Njord",
    ],
    "biblical": [
        "David",
        "Goliath",
        "Saul",
        "Samson",
        "Joshua",
        "Gideon",
        "Joab",
        "Jonathan",
        "Absalom",
        "Benaiah",
        "JudahMaccabee",
    ],
    "iliad": [
        "Achilles", "Hector", "Agamemnon", "Menelaus", "Odysseus", "Ajax",
        "Diomedes", "Patroclus", "Priam", "Paris", "Aeneas", "Nestor",
    ],
    "lotr": [
        "Frodo", "Sam", "Merry", "Pippin", "Bilbo",
        "Gandalf", "Saruman",
        "Aragorn", "Boromir", "Faramir", "Theoden", "Eomer",
        "Legolas", "Elrond",
        "Gimli", "Gloin", "Thorin",
    ],
    "hellenic": [
        "Leonidas", "Pausanias", "Themistocles", "Miltiades", "Pericles",
        "Lysander", "Alcibiades", "Brasidas",
        "Philip", "Alexander", "Hephaestion", "Ptolemy",
        "Cyrus", "Darius", "Xerxes", "Croesus",
    ],
    "natives": [
        "SittingBull",
        "CrazyHorse",
        "Geronimo",
        "ChiefJoseph",
        "Tecumseh",
        "Cochise",
    ],
    "asia": [
        "GenghisKhan",
        "KublaiKhan",
        "SunTzu",
        "LuBu",
        "CaoCao",
        "GuanYu",
        "ZhugeLiang",
        "Musashi",
        "OdaNobunaga",
        "TokugawaIeyasu",
        "MarcoPolo",
    ],
    "women": [
        # arthurian
        "Guinevere", "MorganLeFay", "Isolde", "Vivien",
        # robinhood
        "Marian",
        # beowulf
        "Wealhtheow",
        # chaucer
        "Emily", "Alisoun", "Griselda",
        # eddas
        "Freyja", "Sif", "Frigg", "Skadi",
        # iliad
        "Andromache", "Cassandra", "Helen",
        # lotr
        "Eowyn", "Galadriel", "Arwen",
        # hellenic
        "Olympias", "Artemisia",
        # historical (not from any other theme)
        "Boudicca", "Cleopatra", "JoanOfArc", "Mulan",
        "Aethelflaed", "Lakshmibai", "EmpressMatilda",
    ],
}

DEFAULT_THEMES = list(THEMES.keys())

# Every female demo character lives in the women theme — derive the set from there.
FEMALE_NAMES = set(THEMES["women"])

DEMO_EMAIL_DOMAIN = "demo.boffer.local"

ET = ZoneInfo("America/New_York")
LAUNCH_DATE = date(2026, 3, 17)
WEEKDAY_WEIGHTS = [1.0, 1.0, 1.0, 1.2, 2.5, 3.5, 2.5]  # Mon..Sun
GROWTH_RAMP_DAYS = 60  # post-launch ramp cap
PENDING_FRESHNESS_DAYS = 5


def _username_to_email(username: str) -> str:
    """Convert 'MorganLeFay' -> 'morgan_le_fay@demo.boffer.local'.

    Hyphens preserved (Allan-a-Dale).
    """
    snake = []
    for i, ch in enumerate(username):
        if ch.isupper() and i > 0 and (username[i-1].islower() or username[i-1].isdigit()):
            snake.append("_")
        snake.append(ch.lower())
    return f"{''.join(snake)}@{DEMO_EMAIL_DOMAIN}"


def _gender_for(username: str) -> str:
    """Return canonical gender for a demo character."""
    return "Female" if username in FEMALE_NAMES else "Male"


def _resolve_themes(requested: list[str] | None) -> list[str]:
    """Validate and return the theme list to use. Aborts on unknown themes."""
    themes = requested or DEFAULT_THEMES
    unknown = [t for t in themes if t not in THEMES]
    if unknown:
        valid = ", ".join(sorted(THEMES.keys()))
        raise SystemExit(f"Unknown theme(s): {unknown}. Valid themes: {valid}")
    return themes


def _names_for_themes(themes: list[str]) -> list[str]:
    """Flatten selected themes into a single ordered names list."""
    out = []
    for t in themes:
        out.extend(THEMES[t])
    return out


def _parse_date(s: str) -> date:
    """Parse YYYY-MM-DD; argparse type."""
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"Date must be YYYY-MM-DD, got {s!r}")


def _parse_themes(s: str) -> list[str]:
    """Parse comma-separated theme names; argparse type. Validation deferred to runtime."""
    return [t.strip() for t in s.split(",") if t.strip()]


def _win_probability(skill_a: float, skill_b: float) -> float:
    """Bradley-Terry / ELO formula on hidden skills."""
    return 1.0 / (1.0 + 10 ** ((skill_b - skill_a) / 400))


def _pick_winner(user_a: dict, user_b: dict) -> tuple[dict, dict]:
    """Returns (winner, loser) given two users with 'true_skill' fields."""
    p_a_wins = _win_probability(user_a["true_skill"], user_b["true_skill"])
    if random.random() < p_a_wins:
        return user_a, user_b
    return user_b, user_a


def _generate_match_timestamps(n: int, from_date: date, to_date: date) -> list[datetime]:
    """Generate n timezone-aware (ET) datetimes between from_date and to_date.

    Weekend-heavy + growth-ramped (1.0 → 2.5 over the first 60 days post-launch,
    flat 2.5 thereafter). Returned list is sorted ascending — chronological
    processing is mandatory for ELO correctness.

    No generated timestamp will be in the future: any timestamp on `today` whose
    time-of-day rolls past `now` is clamped to a recent past instant (1–120 min
    before now). Without this, running the script at e.g. 9 AM could produce
    matches "reported" at today 22:00 — i.e. in the future.
    """
    if from_date > to_date:
        raise ValueError(f"from_date {from_date} is after to_date {to_date}")

    now_et = datetime.now(ET)

    days = []
    d = from_date
    while d <= to_date:
        days.append(d)
        d += timedelta(days=1)

    weights = []
    for day in days:
        day_idx = (day - LAUNCH_DATE).days
        growth = 1.0 + 1.5 * min(1.0, max(0, day_idx) / GROWTH_RAMP_DAYS)
        weekday = WEEKDAY_WEIGHTS[day.weekday()]
        weights.append(growth * weekday)

    chosen_days = random.choices(days, weights=weights, k=n)

    timestamps = []
    for day in chosen_days:
        hour_float = max(10.0, min(23.0, random.gauss(18.0, 3.0)))
        hours = int(hour_float)
        minutes = int((hour_float - hours) * 60)
        seconds = random.randint(0, 59)
        local_dt = datetime.combine(day, time(hours, minutes, seconds), tzinfo=ET)
        if local_dt > now_et:
            local_dt = now_et - timedelta(minutes=random.uniform(1, 120))
        timestamps.append(local_dt)

    timestamps.sort()
    return timestamps


def _latest_demo_match_date() -> Optional[datetime]:
    """Return the most recent reportedAt across demo matches, or None if none exist.

    Used to auto-resolve --from when not explicitly provided.
    """
    client = create_client()
    profiles_resp = client.from_("profiles").select("id").like("email", f"%@{DEMO_EMAIL_DOMAIN}").execute()
    demo_ids = [r["id"] for r in profiles_resp.data or []]
    if not demo_ids:
        return None

    win_resp = client.from_("Matches").select("reportedAt").in_("winnerId", demo_ids).order("reportedAt", desc=True).limit(1).execute()
    los_resp = client.from_("Matches").select("reportedAt").in_("loserId", demo_ids).order("reportedAt", desc=True).limit(1).execute()

    candidates = []
    if win_resp.data:
        candidates.append(win_resp.data[0]["reportedAt"])
    if los_resp.data:
        candidates.append(los_resp.data[0]["reportedAt"])

    if not candidates:
        return None

    return max(datetime.fromisoformat(c) for c in candidates)


def _resolve_window(from_date: Optional[date], to_date: Optional[date]) -> tuple[date, date, str]:
    """Resolve --from/--to dates. Returns (resolved_from, resolved_to, explanation_string)."""
    today_et = datetime.now(ET).date()

    if to_date is None:
        to_date = today_et

    if from_date is not None:
        explanation = f"--from explicit: {from_date}"
    else:
        latest = _latest_demo_match_date()
        if latest is not None:
            from_date = latest.astimezone(ET).date()
            explanation = f"--from auto-detected: {from_date} (latest existing match)"
        else:
            from_date = LAUNCH_DATE
            explanation = f"--from defaulted to launch: {from_date}"

    return from_date, to_date, explanation


def create_demo_matches(users: list[dict], n: int, from_date: date, to_date: date) -> dict:
    """Generate n matches between demo users in [from_date, to_date], chronologically ordered.

    Returns count dict: {'confirmed': int, 'pending': int, 'rejected': int}.
    """
    if len(users) < 2:
        raise RuntimeError(f"Need at least 2 demo users, have {len(users)}.")

    client = create_client()  # MUST be lazy — see lazy-client pattern note in plan
    ruleset_resp = client.from_("rule_sets").select("id").execute()
    ruleset_ids = [r["id"] for r in ruleset_resp.data]
    if not ruleset_ids:
        raise RuntimeError("No rule_sets — apply migration first.")

    timestamps = _generate_match_timestamps(n, from_date, to_date)
    now_local = datetime.now(ET)
    counts = {"confirmed": 0, "pending": 0, "rejected": 0}

    for i, reported_at in enumerate(timestamps):
        user_a, user_b = random.sample(users, 2)
        winner, loser = _pick_winner(user_a, user_b)

        reported_at_utc = reported_at.astimezone(timezone.utc).isoformat()
        rule_set_id = random.choice(ruleset_ids)

        report_resp = client.rpc("report_match", {
            "p_winner_id":     winner["id"],
            "p_loser_id":      loser["id"],
            "p_reporter_id":   winner["id"],
            "p_reporter_name": winner["username"],
            "p_reported_at":   reported_at_utc,
            "p_rule_set_id":   rule_set_id,
        }).execute()

        if not report_resp.data:
            raise RuntimeError(f"report_match RPC returned no data at i={i}")
        match_id = report_resp.data[0]["id"]

        r = random.random()
        is_recent = (now_local - reported_at) <= timedelta(days=PENDING_FRESHNESS_DAYS)

        if r < 0.90 or (r < 0.97 and not is_recent):
            # Confirm — pick a time in [reported_at + 5min, min(reported_at + 24h, now)].
            # If no valid window exists (match too recent for 5-min confirm delay), keep pending.
            earliest = reported_at + timedelta(minutes=5)
            latest = min(reported_at + timedelta(hours=24), now_local)
            if earliest >= latest:
                counts["pending"] += 1
            else:
                confirmed_at = earliest + timedelta(seconds=random.uniform(0, (latest - earliest).total_seconds()))
                client.rpc("confirm_match_and_update_elo", {
                    "p_match_id":          match_id,
                    "p_confirmed_at":      confirmed_at.astimezone(timezone.utc).isoformat(),
                    "p_confirmed_by_id":   loser["id"],
                    "p_confirmed_by_name": loser["username"],
                }).execute()
                counts["confirmed"] += 1
        elif r < 0.97:
            # Pending (recent, leave as-is)
            counts["pending"] += 1
        else:
            # Reject — same clamp as confirm but with a 12h cap instead of 24h.
            earliest = reported_at + timedelta(minutes=5)
            latest = min(reported_at + timedelta(hours=12), now_local)
            if earliest >= latest:
                counts["pending"] += 1
            else:
                rejected_at = earliest + timedelta(seconds=random.uniform(0, (latest - earliest).total_seconds()))
                client.rpc("reject_match", {
                    "p_match_id":         match_id,
                    "p_rejected_at":      rejected_at.astimezone(timezone.utc).isoformat(),
                    "p_rejected_by_id":   loser["id"],
                    "p_rejected_by_name": loser["username"],
                }).execute()
                counts["rejected"] += 1

        if (i + 1) % 100 == 0:
            print(f"  [{i + 1}/{len(timestamps)}] matches inserted")

    return counts


def create_demo_users(themes: list[str]) -> list[dict]:
    """Create demo users for the selected themes. Returns list of {id, email, username, true_skill}.

    Each user gets:
      - email_confirm: True (required to appear on leaderboard / public profile)
      - gender from FEMALE_NAMES mapping (deterministic per character)
      - random other preferences (preferredGame, preferredWeapon, preferredShield)
      - hidden 'true_skill' from N(1000, 150) — NOT stored in DB
    """
    # Build the client lazily — initialize.py's module-level client is built at
    # import time before load_dotenv runs, so it points at .env (prod). Calling
    # create_client() here reads env vars after load_dotenv has applied test.env.
    client = create_client()

    ruleset_resp = client.from_("rule_sets").select("name").execute()
    game_names = [r["name"] for r in ruleset_resp.data]
    if not game_names:
        raise RuntimeError("No rule_sets found — apply rule_sets migration first.")

    password = os.environ.get("TEST_PASSWORD", "TestPassword123!")
    now_iso = datetime.now(timezone.utc).isoformat()
    names = _names_for_themes(themes)
    created = []

    for i, username in enumerate(names):
        email = _username_to_email(username)

        user_resp = client.auth.admin.create_user({
            "email":         email,
            "password":      password,
            "email_confirm": True,
        })
        user_id = user_resp.user.id

        client.from_("profiles").update({
            "username":        username,
            "elo":             1000,
            "wins":            0,
            "losses":          0,
            "role_id":         ROLE_MAP["user"],
            "termsAcceptedAt": now_iso,
            "gender":          _gender_for(username),
            "preferredGame":   random.choice(game_names),
            "preferredWeapon": random.choice(["One Handed Sword", "Two Handed Sword", "One Handed Spear", "Two Handed Spear", "Bow", "Javelin"]),
            "preferredShield": random.choice(["None", "Back", "Hand (grip)", "Hand (strap)", "Arm", "Shoulder"]),
        }).eq("id", user_id).execute()

        true_skill = random.gauss(1000, 150)
        created.append({"id": user_id, "email": email, "username": username, "true_skill": true_skill})
        print(f"[{i + 1}/{len(names)}] Created {username} ({email}, {_gender_for(username)})")

    return created


def cmd_users(args):
    themes = _resolve_themes(args.themes)
    names = _names_for_themes(themes)
    _safety_prompt(
        args.env_file,
        f"Will create {len(names)} demo users across themes: {', '.join(themes)}.",
        args.yes,
    )
    users = create_demo_users(themes)
    print(f"\nDone. {len(users)} users created.")


def cmd_matches(args):
    client = create_client()  # MUST be lazy
    # Fetch existing demo users
    resp = client.from_("profiles").select("id, username, email").like("email", f"%@{DEMO_EMAIL_DOMAIN}").execute()
    if len(resp.data) < 2:
        raise RuntimeError(f"Need demo users created first (found {len(resp.data)}).")

    users = [
        {**u, "true_skill": random.gauss(1000, 150)}
        for u in resp.data
    ]

    # Resolve date window
    from_date, to_date, explanation = _resolve_window(args.from_date, args.to_date)
    print(explanation)

    # Resolve count — scale by window length to keep matches-per-day roughly constant
    # across full-window runs and shorter top-up runs. 60 = launch-window baseline.
    window_days = (to_date - from_date).days + 1
    count = args.count if args.count is not None else round(10 * len(users) * window_days / 60)

    _safety_prompt(
        args.env_file,
        f"Will create {count} matches between {from_date} and {to_date} ({window_days}-day window).\n"
        f"Pool: {len(users)} existing demo users.",
        args.yes,
    )

    counts = create_demo_matches(users, count, from_date, to_date)
    print(f"\nDone. {counts}")


def reset_demo() -> dict:
    """Delete all demo users (any theme) and their matches. Returns count dict.

    Order:
      1. SELECT demo user IDs + emails by `@demo.boffer.local` filter.
      2. Defense-in-depth: assert every email ends in @DEMO_EMAIL_DOMAIN.
      3. Hard cap: assert count <= sum of all theme sizes (auto-tracking).
      4. DELETE all Matches where winnerId OR loserId is a demo user.
      5. Delete each demo auth user (cascades to profiles).

    Matches MUST be deleted before users — otherwise the
    reassign_matches_on_profile_delete trigger would auto-reject pending
    demo matches and reassign FK columns to the [deleted] sentinel.

    Reset is theme-agnostic (always wipes everything matching the marker).
    """
    client = create_client()
    resp = client.from_("profiles").select("id, email").like("email", f"%@{DEMO_EMAIL_DOMAIN}").execute()
    rows = resp.data or []

    if not rows:
        print("No demo users to delete.")
        return {"users_deleted": 0, "matches_deleted": 0}

    # Defense-in-depth
    for r in rows:
        if not r["email"].endswith(f"@{DEMO_EMAIL_DOMAIN}"):
            raise RuntimeError(f"Filter leak: {r['email']!r} does not end in @{DEMO_EMAIL_DOMAIN}")

    # Hard cap — total across all themes
    catalog_size = sum(len(t) for t in THEMES.values())
    if len(rows) > catalog_size:
        raise RuntimeError(
            f"Refusing to delete {len(rows)} demo users (cap is {catalog_size} = sum of theme sizes). "
            "Manual investigation required."
        )

    demo_ids = [r["id"] for r in rows]
    print(f"Deleting matches involving {len(demo_ids)} demo users...")

    win_resp = client.from_("Matches").delete().in_("winnerId", demo_ids).execute()
    los_resp = client.from_("Matches").delete().in_("loserId", demo_ids).execute()
    matches_deleted = len(win_resp.data or []) + len(los_resp.data or [])

    print(f"Deleted {matches_deleted} match rows.")
    print(f"Deleting {len(demo_ids)} demo users...")

    for uid, email in zip(demo_ids, (r["email"] for r in rows)):
        client.auth.admin.delete_user(uid)
        print(f"  deleted {email}")

    return {"users_deleted": len(demo_ids), "matches_deleted": matches_deleted}


def verify_demo() -> None:
    """Print verification counts. Read-only; safe to run any time."""
    client = create_client()

    print("=" * 60)
    print(f"Verifying demo data (filter: email LIKE '%@{DEMO_EMAIL_DOMAIN}')")
    print("=" * 60)

    users_resp = client.from_("profiles").select("id, gender, elo").like("email", f"%@{DEMO_EMAIL_DOMAIN}").execute()
    users = users_resp.data or []
    print(f"Demo users:          {len(users)}")

    if not users:
        print("=" * 60)
        return

    elos = [r["elo"] for r in users]
    avg = sum(elos) / len(elos)
    print(f"ELO min / max / avg: {min(elos)} / {max(elos)} / {avg:.0f}")

    from collections import Counter
    gender_counts = Counter(r["gender"] for r in users)
    print(f"Gender:              {dict(gender_counts)}")

    user_ids = [r["id"] for r in users]
    # Use count="exact" to bypass PostgREST's max_rows ceiling (a server-side cap
    # that overrides client .limit()). Demo users only play each other in this
    # seeder, so filtering by winnerId alone gives the same count as winnerId
    # OR loserId — no dedupe needed.
    total_resp = client.from_("Matches").select("id", count="exact").in_("winnerId", user_ids).execute()
    confirmed_resp = client.from_("Matches").select("id", count="exact").in_("winnerId", user_ids).not_.is_("confirmedAt", "null").execute()
    rejected_resp = client.from_("Matches").select("id", count="exact").in_("winnerId", user_ids).not_.is_("rejectedAt", "null").execute()

    total = total_resp.count or 0
    confirmed = confirmed_resp.count or 0
    rejected = rejected_resp.count or 0
    pending = total - confirmed - rejected

    print(f"Demo matches:        {total}")
    print(f"  confirmed:         {confirmed}")
    print(f"  pending:           {pending}")
    print(f"  rejected:          {rejected}")
    print("=" * 60)


def cmd_all(args):
    client = create_client()
    themes = _resolve_themes(args.themes)
    names = _names_for_themes(themes)
    today_et = datetime.now(ET).date()
    window_days = (today_et - LAUNCH_DATE).days + 1
    count = args.count if args.count is not None else round(10 * len(names) * window_days / 60)

    # Pre-flight summary
    resp = client.from_("profiles").select("id, email").like("email", f"%@{DEMO_EMAIL_DOMAIN}").execute()
    existing = resp.data or []
    summary_emails = "\n".join(f"  {r['email']}" for r in existing[:5])
    more = f"\n  ... ({len(existing) - 5} more)" if len(existing) > 5 else ""
    summary = f"Will delete:  {len(existing)} existing demo users + their matches"
    if existing:
        summary += f"\n              First few:\n{summary_emails}{more}"
    summary += (
        f"\nWill create:  {len(names)} demo users across themes: {', '.join(themes)}"
        f"\n              + {count} demo matches (launch → today)"
    )

    _safety_prompt(args.env_file, summary, args.yes)

    # Reset existing demo data
    if existing:
        reset_counts = reset_demo()
        print(f"Reset complete: {reset_counts}\n")

    # Create users — preserves in-memory true_skill values
    users = create_demo_users(themes)
    print()

    # Create matches with the SAME hidden skills used at creation
    match_counts = create_demo_matches(users, count, LAUNCH_DATE, today_et)
    print(f"\nDone. {match_counts}")


def cmd_reset(args):
    client = create_client()
    resp = client.from_("profiles").select("id, email").like("email", f"%@{DEMO_EMAIL_DOMAIN}").execute()
    rows = resp.data or []
    summary_emails = "\n".join(f"  {r['email']}" for r in rows[:5])
    more = f"\n  ... ({len(rows) - 5} more)" if len(rows) > 5 else ""
    summary = (
        f"Will delete {len(rows)} demo users:\n{summary_emails}{more}\n"
        f"Will delete all matches involving these users."
    )
    _safety_prompt(args.env_file, summary, args.yes)
    counts = reset_demo()
    print(f"\nDone. {counts}")


def cmd_verify(args):
    verify_demo()


def _safety_prompt(env_file: str, action_summary: str, skip: bool) -> None:
    """Print action summary and require confirmation. Exits if declined.

    When the env file path does NOT contain 'test' (case-insensitive), require
    the user to type the literal token 'PROD' rather than y/n. The env-file
    name is the signal because Supabase project hostnames are auto-generated
    IDs (e.g. xzwwpkjfnnmmepuvnelx.supabase.co) and don't contain 'test'.
    """
    if skip:
        print("(--yes flag set, skipping safety prompt)")
        return

    print(action_summary)
    print()

    is_test_env = "test" in env_file.lower()

    if is_test_env:
        reply = input("Continue? (y/n): ").strip().lower()
        if reply != "y":
            print("Aborted.")
            sys.exit(0)
    else:
        print("NON-TEST env detected. To confirm, type PROD (uppercase):")
        reply = input("confirm: ").strip()
        if reply != "PROD":
            print("Confirmation token mismatch. Aborted.")
            sys.exit(0)


def main():
    # Shared flags via parent parser — must come AFTER the subcommand.
    # (Adding parents=[common] to the top-level parser triggers an argparse quirk where
    # the subparser's default silently overwrites the top-level value. E.g.
    # `seed_demo --env-file test.env users` would load `.env` (the subparser default)
    # instead of `test.env`. We require flags after the subcommand for predictable behavior.)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--env-file", default=".env", help="Path to .env file (default: .env)")
    common.add_argument("--yes", action="store_true", help="Skip safety prompts (CI/scripting only)")

    parser = argparse.ArgumentParser(description="Seed demo data into the Boffer ELO DB.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_users = sub.add_parser("users", parents=[common], help="Create demo users for selected themes")
    p_users.add_argument("--themes", type=_parse_themes, default=None,
                         help="Comma-separated theme names (default: all defined themes)")

    p_matches = sub.add_parser("matches", parents=[common], help="Add demo matches in a date window")
    p_matches.add_argument("--from", dest="from_date", type=_parse_date, default=None,
                           help="Start date YYYY-MM-DD (default: latest existing match, or launch date)")
    p_matches.add_argument("--to", dest="to_date", type=_parse_date, default=None,
                           help="End date YYYY-MM-DD (default: today)")
    p_matches.add_argument("--count", type=int, default=None,
                           help="Number of matches (default: 10 × n_demo_users)")

    p_all = sub.add_parser("all", parents=[common], help="Reset + create users + create matches")
    p_all.add_argument("--themes", type=_parse_themes, default=None,
                       help="Comma-separated theme names (default: all defined themes)")
    p_all.add_argument("--count", type=int, default=None,
                       help="Number of matches (default: 10 × n_demo_users)")

    sub.add_parser("reset",  parents=[common], help="Delete all demo users and their matches")
    sub.add_parser("verify", parents=[common], help="Print verification counts and distributions")

    args = parser.parse_args()
    load_dotenv(args.env_file, override=True)

    api_url = os.environ.get("API_URL")
    if not api_url:
        print(f"ERROR: API_URL not set after loading {args.env_file}", file=sys.stderr)
        sys.exit(1)

    print(f"Target host: {api_url}")
    print(f"Command:     {args.command}")
    print()

    {
        "users":   cmd_users,
        "matches": cmd_matches,
        "all":     cmd_all,
        "reset":   cmd_reset,
        "verify":  cmd_verify,
    }[args.command](args)


def _self_check_catalog():
    # Every name conforms to the username regex [a-zA-Z0-9_-]{3,24}
    all_names = []
    for theme, names in THEMES.items():
        for name in names:
            assert 3 <= len(name) <= 24, f"{theme}/{name!r} fails 3-24 char check"
            assert all(c.isalnum() or c in "_-" for c in name), f"{theme}/{name!r} has invalid chars"
            all_names.append(name)

    # No duplicates across themes
    seen = {}
    for theme, names in THEMES.items():
        for name in names:
            if name in seen:
                raise AssertionError(f"Duplicate name {name!r} in themes {seen[name]} and {theme}")
            seen[name] = theme

    # DEFAULT_THEMES contains only valid keys
    bad = [t for t in DEFAULT_THEMES if t not in THEMES]
    assert not bad, f"DEFAULT_THEMES contains unknown theme(s): {bad}"

    # FEMALE_NAMES is a subset of all theme names (catches typos)
    orphans = FEMALE_NAMES - set(all_names)
    assert not orphans, f"FEMALE_NAMES contains names not in any theme: {orphans}"

    # Email derivation spot-checks
    samples = {
        "Arthur":          "arthur@demo.boffer.local",
        "MorganLeFay":     "morgan_le_fay@demo.boffer.local",
        "RobinHood":       "robin_hood@demo.boffer.local",
        "Allan-a-Dale":    "allan-a-dale@demo.boffer.local",
        "GuyOfGisbourne":  "guy_of_gisbourne@demo.boffer.local",
        "EmpressMatilda":  "empress_matilda@demo.boffer.local",
    }
    for username, expected in samples.items():
        got = _username_to_email(username)
        assert got == expected, f"{username}: got {got!r}, expected {expected!r}"

    # Gender mapping spot-checks
    assert _gender_for("Arthur") == "Male"
    assert _gender_for("Guinevere") == "Female"
    assert _gender_for("Saladin") == "Male"
    assert _gender_for("Boudicca") == "Female"
    assert _gender_for("Helen") == "Female"


_self_check_catalog()


if __name__ == "__main__":
    main()
