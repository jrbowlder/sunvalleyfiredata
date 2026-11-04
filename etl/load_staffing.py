#!/usr/bin/env python3
"""Load the First Due staffing side into the warehouse.

Sources (data/raw/):
  fd_shift_coverage.json      one row per person-day-assignment, Dec 2024 -> published 2026
  fd_certifications.json      certifications with issue/expiration dates
  fd_groups.json              current group membership snapshot (EMT / MEDIC / POC / ...)
  fd_personnel_shift.json     headcount by shift A/B/C

Curated input (data/curated/):
  dim_person_medic.csv        hand-checked medic status with provenance, 11 people

Design notes:
  * The schedule carries names only, so people are keyed on a normalised
    "First Last" string. Certifications use "Last, First" and are flipped.
  * Group membership is a snapshot with no history - it is stored with the pull
    date and must never be applied backwards in time.
  * Station coverage = assignment Elkhorn/Greenhorn, excluding training. Paid-on-call
    medics only ever appear as 'POC Shift' rows, so work type is not filtered further.

Paths are overridable so a synthetic build can't touch real data: SVFD_RAW, SVFD_DB,
SVFD_CURATED, SVFD_CLEAN (defaults to the 'clean' sibling of SVFD_RAW).
"""
from __future__ import annotations

import csv
import json
import os
import sqlite3
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.abspath(os.environ.get("SVFD_RAW") or os.path.join(ROOT, "data", "raw"))
DB = os.path.abspath(os.environ.get("SVFD_DB") or os.path.join(ROOT, "warehouse.db"))
CURATED = os.path.abspath(os.environ.get("SVFD_CURATED") or os.path.join(ROOT, "data", "curated"))
CLEAN = os.path.abspath(os.environ.get("SVFD_CLEAN")
                        or os.path.join(os.path.dirname(RAW), "clean"))
ASOF = "2026-10-31"
STATION_ASSIGNMENTS = {"elkhorn": "ELK", "greenhorn": "GH", "griffin butte": "GB"}

SCHEMA = """
DROP VIEW  IF EXISTS v_station_day;
DROP TABLE IF EXISTS fact_staffing_day;
DROP TABLE IF EXISTS dim_person;
DROP TABLE IF EXISTS dim_person_medic;
DROP TABLE IF EXISTS dim_certification;
DROP TABLE IF EXISTS dim_person_group;
DROP TABLE IF EXISTS dim_shift;

CREATE TABLE dim_person (
    person_key   TEXT PRIMARY KEY,     -- normalised "First Last"
    display_name TEXT,
    in_schedule  INTEGER DEFAULT 0,
    in_certs     INTEGER DEFAULT 0,
    in_groups    INTEGER DEFAULT 0
);

CREATE TABLE dim_shift (shift_code TEXT PRIMARY KEY, headcount INTEGER, pct REAL);

CREATE TABLE dim_person_medic (
    person_key  TEXT PRIMARY KEY,
    medic_since TEXT,                  -- ISO date
    basis       TEXT,                  -- certification | roster_confirmed | estimated
    note        TEXT
);

CREATE TABLE dim_certification (
    cert_id     INTEGER PRIMARY KEY,
    person_key  TEXT,
    cert_name   TEXT,
    issue_date  TEXT,
    expiry_date TEXT,
    is_paramedic INTEGER
);

CREATE TABLE dim_person_group (
    person_key   TEXT,
    group_code   TEXT,
    group_name   TEXT,
    observed_on  TEXT,                 -- snapshot date: membership has no history
    PRIMARY KEY (person_key, group_code)
);

CREATE TABLE fact_staffing_day (
    staffing_id     INTEGER PRIMARY KEY,
    person_key      TEXT,
    work_date       TEXT,
    year            INTEGER,
    month           TEXT,
    assignment_raw  TEXT,
    station_code    TEXT,              -- only for station assignments
    work_type       TEXT,
    qualifier       TEXT,
    rank            TEXT,
    is_station_coverage INTEGER,       -- 1 = counts as station staffing
    is_actual       INTEGER            -- 0 = published future schedule
);

CREATE INDEX idx_staff_date ON fact_staffing_day(work_date);
CREATE INDEX idx_staff_person ON fact_staffing_day(person_key);
"""


def norm(first, last):
    return f"{(first or '').strip()} {(last or '').strip()}".strip()


def flip(last_first):
    s = (last_first or "").strip()
    if "," in s:
        last, first = [x.strip() for x in s.split(",", 1)]
        return f"{first} {last}"
    return s


def norm_date(v):
    """Schedule dates arrive as '2026-07-31' or 'Oct 1, 2025'."""
    s = (v or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def load_json(name):
    path = os.path.join(RAW, name)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data.get("rows", [])
    return data


def main():
    if not os.path.exists(DB):
        raise SystemExit(f"no warehouse at {DB} - run etl/build.py first")
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)

    schedule = load_json("fd_shift_coverage.json")
    certs = load_json("fd_certifications.json")
    groups = load_json("fd_groups.json")
    shifts = load_json("fd_personnel_shift.json")

    people = {}

    def touch(name):
        name = (name or "").strip()
        if not name:
            return None
        key = name.lower()
        people.setdefault(key, {"display": name, "sched": 0, "cert": 0, "grp": 0})
        return key

    # ---------------------------------------------------------------- schedule
    n_rows = n_station = n_future = 0
    n_dup = 0
    seen = set()
    for r in schedule:
        date = norm_date(r.get("s__work_date"))
        if not date:
            continue
        name = norm(r.get("s__first_name"), r.get("s__last_name"))
        key = touch(name)
        if not key:
            continue
        people[key]["sched"] = 1
        assignment = (r.get("s__assignment_name") or "").strip()
        work_type = (r.get("s__work_type_name") or "").strip()
        qualifier = (r.get("s__qualifier_name") or "").strip()
        rank = (r.get("s__personnel_rank") or "").strip()
        # the First Due export repeats identical rows; dedupe on the full tuple so a
        # genuine same-day trade/overtime pair survives but a copy does not.
        sig = (key, date, assignment, work_type, qualifier, rank)
        if sig in seen:
            n_dup += 1
            continue
        seen.add(sig)
        station = STATION_ASSIGNMENTS.get(assignment.lower())
        is_coverage = 1 if (station and work_type.lower() != "training") else 0
        is_actual = 1 if date <= ASOF else 0
        con.execute(
            """INSERT INTO fact_staffing_day
               (person_key, work_date, year, month, assignment_raw, station_code,
                work_type, qualifier, rank, is_station_coverage, is_actual)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (key, date, int(date[:4]), date[:7], assignment or None, station,
             work_type or None, qualifier or None, rank or None, is_coverage, is_actual),
        )
        n_rows += 1
        n_station += is_coverage
        n_future += 0 if is_actual else 1

    # ---------------------------------------------------------------- certifications
    n_cert = 0
    for r in certs:
        name = flip(r.get("tc_ui__personnel_last_first_name"))
        key = touch(name)
        if not key:
            continue
        people[key]["cert"] = 1
        cert_name = (r.get("ts__certification_name") or "").strip()
        is_para = 1 if ("paramedic" in cert_name.lower() and "basic" not in cert_name.lower()) else 0
        con.execute(
            "INSERT INTO dim_certification (person_key, cert_name, issue_date, expiry_date, is_paramedic)"
            " VALUES (?,?,?,?,?)",
            (key, cert_name or None, r.get("ts__issue_date"), r.get("ts__expiration_date"), is_para),
        )
        n_cert += 1

    # ---------------------------------------------------------------- group snapshot
    n_grp = 0
    for g in groups:
        for m in (g.get("members") or []):
            key = touch(m.get("name"))
            if not key:
                continue
            people[key]["grp"] = 1
            con.execute(
                "INSERT OR REPLACE INTO dim_person_group VALUES (?,?,?,?)",
                (key, (g.get("shortcode") or "").upper(), g.get("name"), ASOF),
            )
            n_grp += 1

    for key, p in people.items():
        con.execute(
            "INSERT OR REPLACE INTO dim_person (person_key, display_name, in_schedule, in_certs, in_groups)"
            " VALUES (?,?,?,?,?)",
            (key, p["display"], p["sched"], p["cert"], p["grp"]),
        )

    # ---------------------------------------------------------------- shifts
    for r in shifts:
        con.execute("INSERT OR REPLACE INTO dim_shift VALUES (?,?,?)",
                    ((r.get("n__personnel_shift_id") or "").strip(),
                     int(float(r.get("col_1") or 0)),
                     float(str(r.get("comp_col_101567") or "0").replace("%", ""))))

    # ---------------------------------------------------------------- curated medic list
    medic_path = os.path.join(CURATED, "dim_person_medic.csv")
    if not os.path.exists(medic_path):
        medic_path = os.path.join(CLEAN, "dim_person_medic.csv")
    n_medic = 0
    if os.path.exists(medic_path):
        with open(medic_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                key = (row.get("person") or "").strip().lower()
                if not key:
                    continue
                con.execute("INSERT OR REPLACE INTO dim_person_medic VALUES (?,?,?,?)",
                            (key, row.get("medic_since"), row.get("basis"), row.get("note")))
                n_medic += 1

    # ---------------------------------------------------------------- daily coverage view
    con.execute("""
    CREATE VIEW v_station_day AS
    SELECT s.work_date,
           s.station_code,
           s.year,
           s.is_actual,
           COUNT(DISTINCT s.person_key) AS headcount,
           MAX(CASE WHEN m.person_key IS NOT NULL
                     AND s.work_date >= m.medic_since THEN 1 ELSE 0 END) AS has_paramedic
    FROM fact_staffing_day s
    LEFT JOIN dim_person_medic m
           ON m.person_key = s.person_key
          AND m.basis IN ('certification','roster_confirmed')
    WHERE s.is_station_coverage = 1
      AND s.station_code IS NOT NULL
    GROUP BY s.work_date, s.station_code, s.year, s.is_actual
    """)
    con.commit()

    print(f"  staffing rows        {n_rows:>6}  (station coverage {n_station}, future/published {n_future}, duplicates dropped {n_dup})")
    print(f"  people               {len(people):>6}")
    print(f"  certifications       {n_cert:>6}")
    print(f"  group memberships    {n_grp:>6}")
    print(f"  medic dimension      {n_medic:>6}")
    print(f"  shifts               {con.execute('SELECT COUNT(*) FROM dim_shift').fetchone()[0]:>6}")
    print("  staffing by year:", dict(con.execute(
        "SELECT year, COUNT(*) FROM fact_staffing_day GROUP BY 1 ORDER BY 1").fetchall()))
    print("  station coverage days:", dict(con.execute(
        "SELECT station_code, COUNT(*) FROM v_station_day GROUP BY 1").fetchall()))
    con.close()


if __name__ == "__main__":
    main()
