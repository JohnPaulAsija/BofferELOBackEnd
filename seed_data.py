import argparse
import os
import random
import time
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from initialize import client
from helpers import ROLE_MAP

load_dotenv()


def create_test_users(n_users: int = 0, n_admins: int = 0, n_super_admins: int = 0) -> list[dict]:
    """
    Create test Supabase auth accounts + profiles.

    Args:
        n_users:        number of 'user' role accounts to create
        n_admins:       number of 'admin' role accounts to create
        n_super_admins: number of 'superAdmin' role accounts to create

    Returns:
        List of dicts with keys: id, email, username, role
    """
    accounts = (
        [("user",       ROLE_MAP["user"])]       * n_users +
        [("admin",      ROLE_MAP["admin"])]      * n_admins +
        [("superAdmin", ROLE_MAP["superAdmin"])] * n_super_admins
    )

    if not accounts:
        print("No accounts requested. Use --users, --admins, or --superadmins.")
        return []

    password = os.getenv("TEST_PASSWORD", "TestPassword123!")

    ts = int(time.time())
    created = []

    for i, (role_name, role_id) in enumerate(accounts):
        email    = f"test_{role_name}_{ts}_{i}@test.com"
        username = f"test_{role_name}_{ts}_{i}"

        user_resp = client.auth.admin.create_user({
            "email": email,
            "password": password,
            "email_confirm": True,
        })
        user_id = user_resp.user.id

        client.from_("profiles").update({
            "username":        username,
            "elo":             1000,
            "wins":            0,
            "losses":          0,
            "role_id":         role_id,
            "termsAcceptedAt": datetime.now(timezone.utc).isoformat(),
            "gender":          random.choice(["Male", "Female", "Other"]),
            "preferredGame":   random.choice(["Hearthlight", "Dagorhir"]),
            "preferredWeapon": random.choice(["One Handed Sword", "Two Handed Sword", "One Handed Spear", "Two Handed Spear", "Bow", "Javelin"]),
            "preferredShield": random.choice(["None", "Back", "Hand (grip)", "Hand (strap)", "Arm", "Shoulder"]),
        }).eq("id", user_id).execute()

        print(f"[{i + 1}/{len(accounts)}] Created {role_name}: {email}")
        created.append({"id": user_id, "email": email, "username": username, "role": role_name})

    print(f"\nDone. {len(created)} account(s) created.")
    return created


def create_test_matches(n: int = 1, confirmed: bool = False) -> list[dict]:
    """
    Create n test matches by randomly pairing profiles.

    Two distinct profiles are chosen at random for each match. The first is
    treated as the winner (and reporter), the second as the loser (and confirmer
    when confirmed=True).

    Args:
        n:         number of matches to create
        confirmed: if True, set confirmedAt to 1 second after reportedAt;
                   if False, leave confirmedAt NULL (pending)

    Returns:
        List of dicts with keys: id, winner, loser, confirmed
    """
    resp = client.from_("profiles").select("id, username, email, elo, wins, losses").execute()
    profiles = resp.data

    if len(profiles) < 2:
        print(f"Need at least 2 profiles to create matches (found {len(profiles)}).")
        return []

    # Fetch available rulesets
    ruleset_resp = client.from_("rule_sets").select("id").execute()
    ruleset_ids = [r["id"] for r in ruleset_resp.data]
    if not ruleset_ids:
        print("No rulesets found in DB. Run the rulesets migration first.")
        return []

    created = []
    for i in range(n):
        winner, loser = random.sample(profiles, 2)

        winner_name = winner["username"] or winner["email"]
        loser_name  = loser["username"]  or loser["email"]

        reported_at = datetime.now(timezone.utc)

        match_row = {
            "winnerId":        winner["id"],
            "winnerName":      winner_name,
            "loserId":         loser["id"],
            "loserName":       loser_name,
            "winnerEloBefore": winner["elo"],
            "loserEloBefore":  loser["elo"],
            "reporterId":      winner["id"],
            "reporterName":    winner_name,
            "eloChange":       max(1, round(32 * (1 - 1 / (1 + 10 ** ((loser["elo"] - winner["elo"]) / 400))))),
            "reportedAt":      reported_at.isoformat(),
            "ruleSetId":       random.choice(ruleset_ids),
        }

        if confirmed:
            confirmed_at = reported_at + timedelta(seconds=1)
            match_row["confirmedAt"]      = confirmed_at.isoformat()
            match_row["confirmedById"]    = loser["id"]
            match_row["confirmedByName"]  = loser_name

        insert_resp = client.from_("Matches").insert(match_row).execute()
        match_id = insert_resp.data[0]["id"]

        if confirmed:
            delta = match_row["eloChange"]
            client.from_("profiles").update({
                "elo":  winner["elo"] + delta,
                "wins": winner["wins"] + 1,
            }).eq("id", winner["id"]).execute()
            client.from_("profiles").update({
                "elo":    loser["elo"] - delta,
                "losses": loser["losses"] + 1,
            }).eq("id", loser["id"]).execute()
            winner["elo"]   += delta
            winner["wins"]  += 1
            loser["elo"]    -= delta
            loser["losses"] += 1

        status = "confirmed" if confirmed else "unconfirmed"
        print(f"[{i + 1}/{n}] ({status}) {winner_name} vs {loser_name}  (id: {match_id[:8]}…)")
        created.append({"id": match_id, "winner": winner_name, "loser": loser_name, "confirmed": confirmed})

    print(f"\nDone. {len(created)} match(es) created.")
    return created


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed test data into Supabase.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    users_parser = subparsers.add_parser("users", help="Create test user accounts")
    users_parser.add_argument("--users",       type=int, default=0, help="Number of user-role accounts")
    users_parser.add_argument("--admins",      type=int, default=0, help="Number of admin-role accounts")
    users_parser.add_argument("--superadmins", type=int, default=0, help="Number of superAdmin-role accounts")

    matches_parser = subparsers.add_parser("matches", help="Create test matches")
    matches_parser.add_argument("n", type=int, help="Number of matches to create")
    matches_parser.add_argument("--confirmed", action="store_true", help="Mark matches as confirmed")

    args = parser.parse_args()

    if args.command == "users":
        create_test_users(args.users, args.admins, args.superadmins)
    elif args.command == "matches":
        create_test_matches(args.n, args.confirmed)
