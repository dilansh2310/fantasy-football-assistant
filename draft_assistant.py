#!/usr/bin/env python3
"""Live draft assistant for Sleeper fantasy football.

Combines Sleeper's API (league/draft settings, live picks, player pool)
with a FantasyPros rankings CSV export to recommend the best available
player for your team's biggest need, updated as the draft happens.

Usage:
    python3 draft_assistant.py --draft-id 1347634504738541568 \
        --username dilansh2310 --rankings-csv fantasypros_rankings.csv
"""
import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

SLEEPER_API = "https://api.sleeper.app/v1"
PLAYERS_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "players_cache.json")
FANTASY_POS = {"QB", "RB", "WR", "TE", "K", "DEF"}
FLEX_ELIGIBLE = {"RB", "WR", "TE"}
FLEX_SHARE = {"RB": 0.45, "WR": 0.45, "TE": 0.10}


def api_get(path):
    with urllib.request.urlopen(f"{SLEEPER_API}{path}") as r:
        return json.loads(r.read())


def load_players():
    if os.path.exists(PLAYERS_CACHE) and time.time() - os.path.getmtime(PLAYERS_CACHE) < 86400:
        with open(PLAYERS_CACHE) as f:
            return json.load(f)
    print("Fetching full player pool from Sleeper (cached for 24h)...")
    players = api_get("/players/nfl")
    with open(PLAYERS_CACHE, "w") as f:
        json.dump(players, f)
    return players


def normalize_name(name):
    name = name.lower()
    name = re.sub(r"[.'’]", "", name)
    name = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", name)
    name = re.sub(r"[^a-z0-9 ]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def display_name(player):
    if player.get("position") == "DEF":
        first, last = player.get("first_name"), player.get("last_name")
        if first and last:
            return f"{first} {last}"
        return f"{player.get('team')} Defense"
    return player.get("full_name")


def name_aliases(player):
    """All the ways a rankings CSV might spell this player's name."""
    if player.get("position") != "DEF":
        return [a for a in [display_name(player)] if a]
    first, last = player.get("first_name"), player.get("last_name")
    team = player.get("team")
    aliases = [display_name(player)]
    if last:
        aliases.append(last)  # mascot only, e.g. "49ers"
    if team:
        aliases.append(team)  # abbreviation, e.g. "SF"
        aliases.append(f"{team} Defense")
    return [a for a in aliases if a]


def load_rankings(csv_path):
    rankings = {}
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rank_col = next((c for c in fieldnames if c.strip().lower() in ("rk", "rank")), None)
        name_col = next((c for c in fieldnames if "player" in c.strip().lower()), None)
        if not rank_col or not name_col:
            sys.exit(f"Couldn't find rank/player columns in {csv_path}. Columns found: {fieldnames}")
        for row in reader:
            if not row.get(name_col):
                continue  # skip blank/malformed rows some exports include
            name = row[name_col].strip()
            name = re.sub(r"\s+[A-Z]{2,3}$", "", name)  # strip trailing team code some exports add
            try:
                rank = int(row[rank_col])
            except (ValueError, TypeError):
                continue
            rankings[normalize_name(name)] = rank
    return rankings


def build_position_needs(settings):
    return {
        "QB": settings.get("slots_qb", 0),
        "RB": settings.get("slots_rb", 0),
        "WR": settings.get("slots_wr", 0),
        "TE": settings.get("slots_te", 0),
        "FLEX": settings.get("slots_flex", 0),
        "K": settings.get("slots_k", 0),
        "DEF": settings.get("slots_def", 0),
    }


def remaining_needs(my_drafted_positions, position_needs):
    remaining = dict(position_needs)
    drafted = list(my_drafted_positions)
    for pos in ("QB", "RB", "WR", "TE", "K", "DEF"):
        while remaining.get(pos, 0) > 0 and pos in drafted:
            drafted.remove(pos)
            remaining[pos] -= 1
    flex_slots = remaining.get("FLEX", 0)
    while flex_slots > 0:
        leftover = next((p for p in drafted if p in FLEX_ELIGIBLE), None)
        if not leftover:
            break
        drafted.remove(leftover)
        flex_slots -= 1
    remaining["FLEX"] = flex_slots
    return remaining


def replacement_rank(position, num_teams, position_needs):
    starters = position_needs.get(position, 0)
    flex_starters = position_needs.get("FLEX", 0) * FLEX_SHARE.get(position, 0)
    return round(num_teams * (starters + flex_starters))


def snake_pick_numbers(slot, teams, rounds):
    """Every overall pick number that belongs to a given snake-draft slot."""
    numbers = []
    for r in range(1, rounds + 1):
        pos_in_round = slot if r % 2 == 1 else (teams - slot + 1)
        numbers.append((r - 1) * teams + pos_in_round)
    return numbers


def pick_label(pick_no, teams):
    """Overall pick number -> "round.pick" (e.g. 98 in a 12-team draft -> "9.2")."""
    rnd = (pick_no - 1) // teams + 1
    pick_in_round = pick_no - (rnd - 1) * teams
    return f"{rnd}.{pick_in_round}"


def main():
    parser = argparse.ArgumentParser(description="Sleeper live draft assistant")
    parser.add_argument("--draft-id", required=True, help="Sleeper draft_id (works for real or mock drafts)")
    parser.add_argument("--username", required=True, help="Your Sleeper username")
    parser.add_argument("--rankings-csv", required=True, help="Path to a FantasyPros rankings CSV export")
    parser.add_argument("--poll-seconds", type=int, default=2)
    args = parser.parse_args()

    user = api_get(f"/user/{args.username}")
    user_id = user["user_id"]

    draft = api_get(f"/draft/{args.draft_id}")
    settings = draft["settings"]
    num_teams = settings.get("teams", 12)
    position_needs = build_position_needs(settings)
    league_name = draft.get("metadata", {}).get("name", "draft")
    print(f"Connected to '{league_name}' ({num_teams} teams). You are user {args.username}.")

    rounds = settings.get("rounds", 15)
    my_slot = (draft.get("draft_order") or {}).get(user_id)
    my_pick_set = set(snake_pick_numbers(my_slot, num_teams, rounds)) if my_slot else set()
    if not my_slot:
        print("Note: couldn't find your slot in the draft order yet - on-the-clock alerts will be skipped "
              "until the order is set.")

    players = load_players()
    rankings = load_rankings(args.rankings_csv)

    name_to_id = {}
    for pid, p in players.items():
        if p.get("position") not in FANTASY_POS:
            continue
        for alias in name_aliases(p):
            name_to_id[normalize_name(alias)] = pid

    player_rank = {}
    unmatched = []
    for norm_name, rank in rankings.items():
        pid = name_to_id.get(norm_name)
        if pid:
            player_rank[pid] = rank
        else:
            unmatched.append(norm_name)
    if unmatched:
        print(f"Note: {len(unmatched)} ranked players didn't match a Sleeper player (likely name formatting). "
              f"First few: {unmatched[:5]}")

    seen_picks = set()
    my_drafted_positions = []
    first_loop = True
    alerted_pick = None
    print("Watching draft... (Ctrl+C to stop)\n")

    while True:
        try:
            draft = api_get(f"/draft/{args.draft_id}")
            picks = api_get(f"/draft/{args.draft_id}/picks")
        except (urllib.error.URLError, json.JSONDecodeError) as e:
            print(f"Warning: couldn't reach Sleeper ({e}); retrying in {args.poll_seconds}s...")
            time.sleep(args.poll_seconds)
            continue

        if my_slot is None:
            my_slot = (draft.get("draft_order") or {}).get(user_id)
            if my_slot:
                my_pick_set = set(snake_pick_numbers(my_slot, num_teams, rounds))
                print(f"Found your draft slot ({my_slot}) - on-the-clock alerts are now active.")

        drafted_ids = {p["player_id"] for p in picks}
        new_picks = sorted((p for p in picks if p["pick_no"] not in seen_picks), key=lambda x: x["pick_no"])

        for p in new_picks:
            seen_picks.add(p["pick_no"])
            meta = p.get("metadata") or {}
            pos = meta.get("position", "")
            if p.get("picked_by") == user_id:
                my_drafted_positions.append(pos)

        next_pick_no = len(picks) + 1
        on_the_clock = next_pick_no in my_pick_set and alerted_pick != next_pick_no
        if on_the_clock:
            alerted_pick = next_pick_no

        if new_picks or first_loop or on_the_clock:
            first_loop = False
            if on_the_clock:
                print(f"\n{'=' * 40}\nON THE CLOCK: pick {pick_label(next_pick_no, num_teams)} is yours.\n{'=' * 40}\n")
            needs = remaining_needs(my_drafted_positions, position_needs)
            open_needs = [pos for pos, n in needs.items() if n > 0]
            print(f"\nYour remaining starting needs: {open_needs or ['starters filled - best player available']}")

            available = []
            best_at_open_need = {}
            for pid, rank in player_rank.items():
                if pid in drafted_ids:
                    continue
                p = players[pid]
                pos = p.get("position")
                repl = replacement_rank(pos, num_teams, position_needs)
                vbd = repl - rank
                need_boost = 15 if (pos in open_needs or (pos in FLEX_ELIGIBLE and "FLEX" in open_needs)) else 0
                entry = (vbd + need_boost, rank, display_name(p), pos, p.get("team") or "FA")
                available.append(entry)
                if pos in open_needs and (pos not in best_at_open_need or rank < best_at_open_need[pos][1]):
                    best_at_open_need[pos] = entry
            available.sort(key=lambda x: (-x[0], x[1]))

            print("Top recommendations:")
            top10 = available[:10]
            for score, rank, name, pos, team in top10:
                print(f"  #{rank:>4}  {name:<25} {pos:<4} {team:<4} (value score {score})")

            top10_ids = {(rank, name) for _, rank, name, _, _ in top10}
            buried_needs = [
                entry for entry in best_at_open_need.values()
                if (entry[1], entry[2]) not in top10_ids
            ]
            if buried_needs:
                print("\n  Needs check (best available at a position you still need to start, even though it scored low):")
                for score, rank, name, pos, team in sorted(buried_needs, key=lambda x: x[1]):
                    print(f"    #{rank:>4}  {name:<25} {pos:<4} {team:<4}  <- thin position, don't count on it lasting")
            print()

        if draft.get("status") == "complete":
            print("Draft complete.")
            break
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
