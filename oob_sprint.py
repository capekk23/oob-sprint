#!/usr/bin/env python3
"""OOB Sprint — tracks last-split times and points for OOB club members."""

import os
import sys
import math
import argparse
import requests
from datetime import date
from tabulate import tabulate

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import psycopg
    PSYCOPG_VERSION = 3
except ImportError:
    try:
        import psycopg2 as psycopg
        PSYCOPG_VERSION = 2
    except ImportError:
        print("Error: install psycopg (v3) or psycopg2 — see requirements.txt")
        sys.exit(1)

ORIS_BASE = "https://oris.ceskyorientak.cz/API/?format=json&method="
OOB_CLUB_ID = 95

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def get_db():
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("Error: DATABASE_URL not set. Add it to a .env file or environment.")
        print("  Example: DATABASE_URL=postgresql://user:pass@localhost/oob_sprint")
        sys.exit(1)
    try:
        conn = psycopg.connect(url)
        return conn
    except Exception as e:
        print(f"Error connecting to database: {e}")
        sys.exit(1)


SCHEMA = """
CREATE TABLE IF NOT EXISTS races (
    id SERIAL PRIMARY KEY,
    oris_id INTEGER UNIQUE NOT NULL,
    name TEXT NOT NULL,
    date DATE NOT NULL,
    discipline TEXT,
    location TEXT
);

CREATE TABLE IF NOT EXISTS members (
    id SERIAL PRIMARY KEY,
    oris_id INTEGER UNIQUE NOT NULL,
    name TEXT NOT NULL,
    reg_number TEXT
);

CREATE TABLE IF NOT EXISTS splits (
    id SERIAL PRIMARY KEY,
    race_id INTEGER REFERENCES races(id),
    member_id INTEGER REFERENCES members(id),
    last_leg_seconds INTEGER NOT NULL,
    club_rank INTEGER NOT NULL,
    points INTEGER NOT NULL,
    UNIQUE(race_id, member_id)
);
"""


def init_db(conn):
    with conn.cursor() as cur:
        cur.execute(SCHEMA)
    conn.commit()


# ---------------------------------------------------------------------------
# ORIS API
# ---------------------------------------------------------------------------

def oris_get(method, **params):
    url = ORIS_BASE + method
    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"Error fetching ORIS data ({method}): {e}")
        sys.exit(1)
    if data.get("Status") != "OK":
        msg = data.get("ExportCreated", "Unknown error")
        print(f"ORIS API error ({method}): {msg}")
        sys.exit(1)
    return data.get("Data", {})


def get_event(event_id):
    return oris_get("getEvent", id=event_id)


def get_event_results(event_id):
    """Returns dict of result entries for OOB club members."""
    return oris_get("getEventResults", eventid=event_id, clubid=OOB_CLUB_ID)


def get_splits(class_id):
    """Returns raw Data dict with keys: Splits, Controls, BestTime."""
    return oris_get("getSplits", classid=class_id)


# ---------------------------------------------------------------------------
# Splits parsing
#
# getSplits Data structure:
#   Data.Splits  → dict keyed "Position1", "Position2", …
#     Each entry has flat fields:
#       ResName, RegNo, ResClub, ResTime, ResLoss, PersID, …
#       SplitTime1, SplitTime2, …, SplitTime999   (leg times, "MM:SS")
#       SplitPlace1, …, SplitPlace999
#       TotalTime1, …, TotalTime999
#     SplitTime999 is the LAST LEG (last control → finish).
#   Data.Controls → dict keyed "Control1", "Control2", …, "Control999"
#   Data.BestTime → string
# ---------------------------------------------------------------------------

def parse_time_to_seconds(t):
    """Parse 'MM:SS' or 'HH:MM:SS' string to int seconds. Returns None if invalid."""
    if not t:
        return None
    t = str(t).strip()
    if not t or t in ("-", "0", ""):
        return None
    parts = t.split(":")
    try:
        if len(parts) == 2:
            val = int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            val = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        else:
            val = int(t)
        return val if val > 0 else None
    except ValueError:
        return None


def extract_last_leg(competitor):
    """
    Given a competitor dict from getSplits Data.Splits, return the last-leg
    time in seconds. SplitTime999 is the finish leg. Returns None for DNF/DSQ.
    """
    # Must have a valid total time (ResTime) — empty/dash means DNF/DSQ
    res_time = (competitor.get("ResTime") or "").strip()
    if not res_time or res_time in ("-", "0", "DNS", "DNF", "DSQ", "MP"):
        return None

    last_leg_str = competitor.get("SplitTime999", "")
    return parse_time_to_seconds(last_leg_str)


def compute_points(winner_time, competitor_time):
    """Hybrid formula: winner=100, others=floor(winner/their × 100), min 10."""
    if not competitor_time or competitor_time <= 0:
        return 10
    raw = math.floor(winner_time / competitor_time * 100)
    return max(10, raw)


def format_seconds(s):
    if s is None:
        return "-"
    return f"{s // 60}:{s % 60:02d}"


# ---------------------------------------------------------------------------
# add command
# ---------------------------------------------------------------------------

def cmd_add(conn, oris_event_id):
    init_db(conn)
    print(f"Fetching event {oris_event_id} from ORIS...")

    # 1. Event info
    event = get_event(oris_event_id)
    event_name = event.get("Name", f"Event {oris_event_id}")
    event_date = event.get("Date", "1970-01-01")
    discipline_obj = event.get("Discipline", {})
    if isinstance(discipline_obj, dict):
        discipline_name = discipline_obj.get("ShortName") or discipline_obj.get("NameCZ", "")
    else:
        discipline_name = str(discipline_obj) if discipline_obj else ""
    location = event.get("Place", "")
    print(f"  {event_name}  |  {event_date}  |  {discipline_name}  |  {location}")

    # 2. Results for OOB club — grouped by ClassID
    print("Fetching OOB member results...")
    results = get_event_results(oris_event_id)
    if not results:
        print("No results found for OOB club in this event.")
        return

    # Build lookup: RegNo -> member info, and collect unique ClassIDs
    # RegNo is the stable identifier since UserID can be null in results
    members_by_regno = {}  # reg_no -> {name, reg_no, class_id, oris_user_id}
    class_ids = set()

    for res in results.values():
        reg_no = (res.get("RegNo") or "").strip()
        name = (res.get("Name") or "").strip()
        class_id = (res.get("ClassID") or "").strip()
        user_id = res.get("UserID")  # may be null/None
        res_time = (res.get("Time") or "").strip()

        if not reg_no or not name:
            continue
        # Skip DNS (no time at all and Place is empty/DNS)
        place = (res.get("Place") or "").strip()
        if place in ("DNS",):
            continue

        members_by_regno[reg_no] = {
            "name": name,
            "reg_no": reg_no,
            "class_id": class_id,
            "oris_user_id": user_id,
            "res_time": res_time,
        }
        if class_id:
            class_ids.add(class_id)

    if not members_by_regno:
        print("No valid OOB member entries found.")
        return

    print(f"  Found {len(members_by_regno)} OOB member(s) across {len(class_ids)} category/ies.")

    # 3. For each category, fetch splits and extract last-leg for OOB members
    print("Fetching splits per category...")
    last_legs = {}  # reg_no -> last_leg_seconds

    for class_id in class_ids:
        splits_data = get_splits(class_id)
        if not splits_data:
            continue

        # splits_data has key "Splits" (and "Controls", "BestTime")
        splits_section = splits_data.get("Splits") or splits_data
        if not isinstance(splits_section, dict):
            continue

        for pos_key, competitor in splits_section.items():
            if not isinstance(competitor, dict):
                continue
            reg_no = (competitor.get("RegNo") or "").strip()
            if reg_no not in members_by_regno:
                continue

            last_leg = extract_last_leg(competitor)
            if last_leg and last_leg > 0:
                last_legs[reg_no] = last_leg
            else:
                print(f"  Skipping {members_by_regno[reg_no]['name']} (DNF/DSQ or no split data)")

    if not last_legs:
        print("No valid last-leg split data found for OOB members.")
        return

    # 4. Rank by last-leg time (fastest first) and compute points
    ranked = sorted(last_legs.items(), key=lambda x: x[1])
    winner_time = ranked[0][1]

    entries = []
    for rank, (reg_no, last_leg) in enumerate(ranked, start=1):
        info = members_by_regno[reg_no]
        pts = compute_points(winner_time, last_leg)
        # Use a synthetic oris_id based on reg_no hash if UserID is None
        oris_uid = info["oris_user_id"]
        if not oris_uid:
            # Use a stable integer derived from reg_no for the members table
            oris_uid = abs(hash(reg_no)) % (10**9)
        entries.append({
            "oris_user_id": int(oris_uid),
            "name": info["name"],
            "reg_no": reg_no,
            "last_leg": last_leg,
            "club_rank": rank,
            "points": pts,
        })

    # 5. Persist to DB
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO races (oris_id, name, date, discipline, location)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (oris_id) DO UPDATE
                SET name=EXCLUDED.name, date=EXCLUDED.date,
                    discipline=EXCLUDED.discipline, location=EXCLUDED.location
            RETURNING id
        """, (oris_event_id, event_name, event_date, discipline_name, location))
        race_id = cur.fetchone()[0]

        for e in entries:
            cur.execute("""
                INSERT INTO members (oris_id, name, reg_number)
                VALUES (%s, %s, %s)
                ON CONFLICT (oris_id) DO UPDATE
                    SET name=EXCLUDED.name, reg_number=EXCLUDED.reg_number
                RETURNING id
            """, (e["oris_user_id"], e["name"], e["reg_no"]))
            member_id = cur.fetchone()[0]

            cur.execute("""
                INSERT INTO splits (race_id, member_id, last_leg_seconds, club_rank, points)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (race_id, member_id) DO UPDATE
                    SET last_leg_seconds=EXCLUDED.last_leg_seconds,
                        club_rank=EXCLUDED.club_rank,
                        points=EXCLUDED.points
            """, (race_id, member_id, e["last_leg"], e["club_rank"], e["points"]))

    conn.commit()
    print(f"Saved {len(entries)} result(s) for race {oris_event_id}.\n")

    # 6. Print results table
    table = [
        [e["club_rank"], e["name"], e["reg_no"], format_seconds(e["last_leg"]), e["points"]]
        for e in entries
    ]
    print(tabulate(table, headers=["Rank", "Name", "Reg", "Last Split", "Points"], tablefmt="rounded_outline"))


# ---------------------------------------------------------------------------
# leaderboard command
# ---------------------------------------------------------------------------

def cmd_leaderboard(conn):
    init_db(conn)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT m.name, m.reg_number,
                   SUM(s.points) AS total_points,
                   COUNT(s.id) AS race_count
            FROM members m
            JOIN splits s ON s.member_id = m.id
            GROUP BY m.id, m.name, m.reg_number
            ORDER BY total_points DESC, race_count DESC, m.name
        """)
        rows = cur.fetchall()

    if not rows:
        print("No data yet. Use 'add <event_id>' to import a race.")
        return

    table = [
        [i + 1, row[0], row[1], row[2], row[3]]
        for i, row in enumerate(rows)
    ]
    print(tabulate(table, headers=["#", "Name", "Reg", "Total Points", "Races"], tablefmt="rounded_outline"))


# ---------------------------------------------------------------------------
# races command
# ---------------------------------------------------------------------------

def cmd_races(conn):
    init_db(conn)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT r.oris_id, r.date, r.name, r.discipline, r.location,
                   COUNT(s.id) AS participants
            FROM races r
            LEFT JOIN splits s ON s.race_id = r.id
            GROUP BY r.id, r.oris_id, r.date, r.name, r.discipline, r.location
            ORDER BY r.date DESC
        """)
        rows = cur.fetchall()

    if not rows:
        print("No races tracked yet.")
        return

    table = [
        [row[0], row[1], row[2], row[3] or "", row[4] or "", row[5]]
        for row in rows
    ]
    print(tabulate(table, headers=["ORIS ID", "Date", "Name", "Discipline", "Location", "Members"], tablefmt="rounded_outline"))


# ---------------------------------------------------------------------------
# day command
# ---------------------------------------------------------------------------

def cmd_day(day_str=None):
    """List all OB races on a given day (default: today) with OOB member counts."""
    if day_str:
        try:
            date.fromisoformat(day_str)
            target = day_str
        except ValueError:
            print(f"Invalid date format: {day_str}. Use YYYY-MM-DD.")
            sys.exit(1)
    else:
        target = date.today().isoformat()

    print(f"Fetching races for {target}...")
    events_data = oris_get("getEventList", sport=1, datefrom=target, dateto=target, all=1)

    if not events_data:
        print("No races found for that day.")
        return []

    events = list(events_data.values()) if isinstance(events_data, dict) else events_data

    print(f"Found {len(events)} race(s). Checking OOB participation...\n")

    rows = []
    for ev in events:
        ev_id = ev.get("ID", "")
        ev_name = ev.get("Name", "")
        ev_place = ev.get("Place", "") or ""
        ev_discipline = (ev.get("Discipline") or {})
        if isinstance(ev_discipline, dict):
            ev_discipline = ev_discipline.get("ShortName", "")
        else:
            ev_discipline = str(ev_discipline)

        # Count OOB members in this race
        try:
            results = oris_get("getEventResults", eventid=ev_id, clubid=OOB_CLUB_ID)
            count = len([r for r in results.values() if isinstance(r, dict)
                         and (r.get("Place") or "").strip() not in ("DNS", "")]) if results else 0
        except SystemExit:
            count = 0

        rows.append({
            "id": ev_id,
            "name": ev_name,
            "discipline": ev_discipline,
            "place": ev_place,
            "count": count,
        })

    # Sort: races with OOB members first
    rows.sort(key=lambda r: -r["count"])

    table = [
        [i + 1, f"{r['name']} ({r['count']})", r["discipline"], r["place"], r["id"]]
        for i, r in enumerate(rows)
    ]
    print(tabulate(table, headers=["#", "Race (OOB members)", "Disc.", "Place", "ORIS ID"], tablefmt="rounded_outline"))
    return rows


# ---------------------------------------------------------------------------
# show command
# ---------------------------------------------------------------------------

def cmd_show(conn, oris_event_id):
    init_db(conn)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, date, discipline, location FROM races WHERE oris_id = %s",
            (oris_event_id,)
        )
        race = cur.fetchone()

    if not race:
        print(f"Race {oris_event_id} not found. Run: python oob_sprint.py add {oris_event_id}")
        return

    race_id, name, date, discipline, location = race
    parts = [str(name), str(date)]
    if discipline:
        parts.append(discipline)
    if location:
        parts.append(location)
    print("  |  ".join(parts) + "\n")

    with conn.cursor() as cur:
        cur.execute("""
            SELECT s.club_rank, m.name, m.reg_number, s.last_leg_seconds, s.points
            FROM splits s
            JOIN members m ON m.id = s.member_id
            WHERE s.race_id = %s
            ORDER BY s.club_rank
        """, (race_id,))
        rows = cur.fetchall()

    if not rows:
        print("No results stored for this race.")
        return

    table = [
        [row[0], row[1], row[2], format_seconds(row[3]), row[4]]
        for row in rows
    ]
    print(tabulate(table, headers=["Rank", "Name", "Reg", "Last Split", "Points"], tablefmt="rounded_outline"))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="OOB Sprint — last-split points tracker for OOB TJ Lokomotiva Trutnov"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="Import a race by ORIS event ID")
    p_add.add_argument("event_id", type=int, help="ORIS event ID")

    sub.add_parser("leaderboard", help="Show cumulative points leaderboard")
    sub.add_parser("races", help="List all tracked races")

    p_show = sub.add_parser("show", help="Show results for a specific race")
    p_show.add_argument("event_id", type=int, help="ORIS event ID")

    p_day = sub.add_parser("day", help="List races on a given day with OOB member counts")
    p_day.add_argument("date", nargs="?", default=None, help="Date in YYYY-MM-DD format (default: today)")

    args = parser.parse_args()

    if args.command == "day":
        cmd_day(getattr(args, "date", None))
        return

    conn = get_db()
    try:
        if args.command == "add":
            cmd_add(conn, args.event_id)
        elif args.command == "leaderboard":
            cmd_leaderboard(conn)
        elif args.command == "races":
            cmd_races(conn)
        elif args.command == "show":
            cmd_show(conn, args.event_id)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
