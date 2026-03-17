# oob-sprint

Last-split time tracker and points system for **OOB TJ Lokomotiva Trutnov** (club 95) orienteering members.

After each race the tool fetches splits from the [ORIS API](https://oris.ceskyorientak.cz), extracts each member's last leg (last control → finish), ranks them, assigns points, and stores everything in PostgreSQL.

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

> Requires Python 3.9+. Uses `psycopg` (v3) with binary extras. If you prefer psycopg2, replace `psycopg[binary]` with `psycopg2-binary` in `requirements.txt`.

### 2. Create a PostgreSQL database

```bash
createdb oob_sprint
```

### 3. Configure the connection

Create a `.env` file in the project directory:

```
DATABASE_URL=postgresql://user:password@localhost/oob_sprint
```

Or export it as an environment variable:

```bash
export DATABASE_URL=postgresql://user:password@localhost/oob_sprint
```

The schema is created automatically on first run.

---

## Usage

### Import a race

```bash
python oob_sprint.py add 8511
```

This will:
1. Fetch event info from ORIS
2. Fetch OOB member results (club 95)
3. Fetch splits for each category containing OOB members
4. Extract each member's last-leg time (last control → finish)
5. Rank by last-leg time (fastest first)
6. Assign points (winner = 100, others = floor(winner_time / their_time × 100), minimum 10)
7. Save everything to the database
8. Print the race results table

Example output:
```
╭──────┬────────────────────┬────────┬────────────┬────────╮
│ Rank │ Name               │ Reg    │ Last Split │ Points │
├──────┼────────────────────┼────────┼────────────┼────────┤
│    1 │ Jan Novák          │ LTU123 │ 1:42       │    100 │
│    2 │ Petra Horáková     │ LTU045 │ 1:48       │     94 │
│    3 │ Tomáš Beneš        │ LTU211 │ 2:05       │     82 │
╰──────┴────────────────────┴────────┴────────────┴────────╯
```

### View the leaderboard

```bash
python oob_sprint.py leaderboard
```

Shows cumulative points across all races, sorted by total.

### List tracked races

```bash
python oob_sprint.py races
```

### Show results for a specific race

```bash
python oob_sprint.py show 8511
```

---

## Points formula

| Situation      | Points                                      |
|----------------|---------------------------------------------|
| Race winner    | 100                                         |
| Other finisher | `floor(winner_time / your_time × 100)`      |
| Minimum        | 10 (for any finisher, even on a rough day)  |
| DNF / DSQ      | Not included (no split data)                |

---

## Database schema

```sql
CREATE TABLE races (
    id SERIAL PRIMARY KEY,
    oris_id INTEGER UNIQUE NOT NULL,
    name TEXT NOT NULL,
    date DATE NOT NULL,
    discipline TEXT,
    location TEXT
);

CREATE TABLE members (
    id SERIAL PRIMARY KEY,
    oris_id INTEGER UNIQUE NOT NULL,
    name TEXT NOT NULL,
    reg_number TEXT
);

CREATE TABLE splits (
    id SERIAL PRIMARY KEY,
    race_id INTEGER REFERENCES races(id),
    member_id INTEGER REFERENCES members(id),
    last_leg_seconds INTEGER NOT NULL,
    club_rank INTEGER NOT NULL,
    points INTEGER NOT NULL,
    UNIQUE(race_id, member_id)
);
```

---

## ORIS API endpoints used

| Method           | Purpose                                    |
|------------------|--------------------------------------------|
| `getEvent`       | Race name, date, discipline, location      |
| `getEventResults`| Results filtered to club 95                |
| `getSplits`      | Per-control split times for a category     |
