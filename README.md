# oob-sprint

CLI tool that tracks **last-split times** (last control → finish) for orienteering club members across races, assigns points, and builds a season leaderboard. Uses the [ORIS API](https://oris.ceskyorientak.cz) — the Czech orienteering results system.

Default club: **OOB TJ Lokomotiva Trutnov** (club ID 95).

---

## Requirements

- Python 3.9+
- PostgreSQL

```bash
pip install -r requirements.txt
```

---

## Setup

**1. Create the database:**

```bash
createdb oob_sprint
```

**2. Create a `.env` file:**

```
DATABASE_URL=postgresql://user:password@localhost/oob_sprint
```

The schema is created automatically on first run.

---

## Changing the club

Edit `OOB_CLUB_ID` near the top of `oob_sprint.py`:

```python
OOB_CLUB_ID = 95  # OOB TJ Lokomotiva Trutnov
```

Look up club IDs via the ORIS API:

```
https://oris.ceskyorientak.cz/API/?format=json&method=getCSOSClubList
```

---

## Commands

### List today's races

```bash
python3 oob_sprint.py day
```

Shows all orienteering races today with a count of club members per race.

### List races on a specific date

```bash
python3 oob_sprint.py day 2025-04-06
```

### Import a race

```bash
python3 oob_sprint.py add 8511
```

Fetches event info and splits from ORIS, computes last-leg times and points for all club members, saves to the database, and prints the results table.

```
╭──────┬────────────────────┬────────┬────────────┬────────╮
│ Rank │ Name               │ Reg    │ Last Split │ Points │
├──────┼────────────────────┼────────┼────────────┼────────┤
│    1 │ Jan Novák          │ LTU123 │ 1:42       │    100 │
│    2 │ Petra Horáková     │ LTU045 │ 1:48       │     94 │
│    3 │ Tomáš Beneš        │ LTU211 │ 2:05       │     82 │
╰──────┴────────────────────┴────────┴────────────┴────────╯
```

### Season leaderboard

```bash
python3 oob_sprint.py leaderboard
```

Cumulative points across all imported races, sorted by total.

### List tracked races

```bash
python3 oob_sprint.py races
```

### Show results for a race

```bash
python3 oob_sprint.py show 8511
```

---

## Points formula

- **Winner:** 100 points
- **Others:** `floor(winner_time / your_time × 100)`
- **Minimum:** 10 points
- **DNF / DSQ:** skipped (no split data)

A 2:00 last leg when the winner ran 1:42 → `floor(102/120 × 100)` = 85 points.
