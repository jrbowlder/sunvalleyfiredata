#!/usr/bin/env python3
"""Build the warehouse from raw Elite pulls.

Reads data/raw/elite_incident_fact.csv and data/raw/elite_apparatus_fact.csv,
normalises identity, and writes warehouse.db with dims + facts.

Stdlib only (csv, sqlite3) so it runs anywhere. Rerun after a new pull; it
rebuilds from scratch every time.

Paths are overridable so a synthetic build can't touch real data:
SVFD_RAW (input dir), SVFD_DB (output database), SVFD_CLEAN (review-file dir,
defaults to the 'clean' sibling of SVFD_RAW).
"""
from __future__ import annotations

import csv
import os
import re
import sqlite3
from datetime import datetime

from taxonomy import classify

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.abspath(os.environ.get("SVFD_RAW") or os.path.join(ROOT, "data", "raw"))
DB = os.path.abspath(os.environ.get("SVFD_DB") or os.path.join(ROOT, "warehouse.db"))
# review files land beside the input dir, so a synthetic build never overwrites the real ones
CLEAN = os.path.abspath(os.environ.get("SVFD_CLEAN")
                        or os.path.join(os.path.dirname(RAW), "clean"))
REVIEW = os.path.join(CLEAN, "identity_review.csv")

# ---------------------------------------------------------------- identity maps

STATION_MAP = {
    "elkhorn station": "ELK",
    "elkhorn station - hq": "ELK",
    "elkhorn": "ELK",
    "station 2": "GH",          # obsolete naming, dropped by the department
    "greenhorn": "GH",
    "station 3": "GB",          # obsolete naming
    "griffin butte": "GB",
}

# apparatus: strip decorations, then canonicalise the leading unit type
APPARATUS_PREFIX = [
    (re.compile(r"^eng(?:ine)?\s*", re.I), "E"),
    (re.compile(r"^e\s*", re.I), "E"),
    (re.compile(r"^tank(?:er)?\s*", re.I), "T"),
    (re.compile(r"^t\s*", re.I), "T"),
    (re.compile(r"^truck\s*", re.I), "T"),
    (re.compile(r"^(?:chief|ch|c)\s*", re.I), "C"),
    (re.compile(r"^sq(?:uad)?\s*", re.I), "SQD"),
    (re.compile(r"^medic\s*", re.I), "M"),
]

# call-type classification lives in etl/taxonomy.py - NFIRS code first, keywords only
# as a fallback. See that module for why the old description regex was wrong.


def canon_station(raw: str | None) -> str | None:
    if not raw:
        return None
    return STATION_MAP.get(raw.strip().lower())


def canon_apparatus(raw: str | None) -> str | None:
    if not raw:
        return None
    s = raw.strip().upper().replace("_", " ")
    for pat, rep in APPARATUS_PREFIX:
        if pat.match(s):
            rest = pat.sub("", s).strip()
            if re.fullmatch(r"\d+[A-Z]?", rest):
                return f"{rep}{rest}"
            break
    return s or None


def parse_dt(v: str | None):
    if not v or not v.strip():
        return None
    v = v.strip()
    for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    return None


def col(row: dict, *frags: str) -> str | None:
    """First column whose header contains every fragment (case-insensitive)."""
    for k in row:
        if all(f.lower() in k.lower() for f in frags):
            return k
    return None


def num(v: str | None):
    if v is None:
        return None
    v = v.strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


SCHEMA = """
DROP TABLE IF EXISTS fact_incident;
DROP TABLE IF EXISTS fact_unit_response;
DROP TABLE IF EXISTS dim_station;
DROP TABLE IF EXISTS dim_apparatus;
DROP TABLE IF EXISTS dim_incident_type;

CREATE TABLE dim_station (station_code TEXT PRIMARY KEY, station_name TEXT);
CREATE TABLE dim_apparatus (
    apparatus_code TEXT PRIMARY KEY,
    raw_spellings  TEXT
);
CREATE TABLE dim_incident_type (
    type_code   TEXT PRIMARY KEY,
    description TEXT,
    family      TEXT,
    subtype     TEXT
);
CREATE TABLE fact_incident (
    incident_uid      INTEGER PRIMARY KEY,  -- surrogate; incident numbers repeat across years
    incident_key      TEXT NOT NULL,        -- source incident number, verbatim
    incident_no_scheme TEXT,              -- 'dash' | '7digit' | 'other'
    incident_dt       TEXT,
    year              INTEGER,
    station_code      TEXT,
    station_raw       TEXT,
    zone_district     TEXT,
    street_name       TEXT,               -- raw address, used for offline geocoding
    street_number     TEXT,
    type_code         TEXT,
    type_desc         TEXT,
    incident_class    TEXT,               -- call-type family (see etl/taxonomy.py)
    incident_subtype  TEXT,               -- drill-down subtype within the family
    mutual_aid_given  TEXT,
    city              TEXT,
    latitude          REAL,
    longitude         REAL,
    alarm_to_arrival_min REAL,
    alarm_to_cleared_min REAL,
    alarm_to_controlled_min REAL,
    psap_to_alarm_min REAL,
    is_actual         INTEGER
);
CREATE TABLE fact_unit_response (
    unit_row_id        INTEGER PRIMARY KEY,
    incident_key       TEXT,
    apparatus_code     TEXT,
    apparatus_raw      TEXT,
    resource_type      TEXT,
    dispatch_dt        TEXT,
    arrival_dt         TEXT,
    cleared_dt         TEXT,
    in_service_dt      TEXT,
    dispatch_to_arrival_min REAL,
    arrived_to_cleared_min  REAL,
    arrived_to_service_min  REAL,
    unit_hours         REAL,
    incident_class     TEXT
);
"""

STATIONS = {
    "ELK": "Elkhorn",
    "GH": "Greenhorn",
    "GB": "Griffin Butte",
}


def main():
    os.makedirs(os.path.dirname(REVIEW), exist_ok=True)
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)

    for code, name in STATIONS.items():
        con.execute("INSERT INTO dim_station VALUES (?,?)", (code, name))

    review = {}   # raw -> canonical, for human eyeballing
    apt_raw = {}

    # ---------------------------------------------------------------- incidents
    with open(os.path.join(RAW, "elite_incident_fact.csv"), newline="", encoding="utf-8", errors="replace") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit("incident CSV is empty")

    c_num = col(rows[0], "Incident Number")
    c_dt = col(rows[0], "Alarm", "Date Time")
    c_st = col(rows[0], "Primary Station")
    c_zone = col(rows[0], "Zone/District Number")
    c_street = col(rows[0], "Street Name")
    c_streetno = col(rows[0], "Street Number")
    c_tc = col(rows[0], "Type Code")
    c_td = col(rows[0], "Basic Incident Type (")
    c_ma = col(rows[0], "Mutual Aid Given")
    c_city = col(rows[0], "City Name")
    c_lat = col(rows[0], "Latitude")
    c_lon = col(rows[0], "Longitude")
    c_aa = col(rows[0], "Alarm To Arrival In Minutes")
    c_ac = col(rows[0], "Alarm To Last Unit Cleared In Minutes")
    c_act = col(rows[0], "Alarm To Controlled In Minutes")
    c_psap = col(rows[0], "PSAP To Alarm In Minutes")

    types = {}
    n_ok = 0

    # The export contains duplicate keys: 78 incident numbers appear more than once
    # (113 extra rows), including 36 rows with a completely blank incident number.
    # Keep the richest row per key - the one with the most populated fields.
    by_key: dict[str, dict] = {}
    blank_keys = 0
    for r in rows:
        key = (r[c_num] or "").strip()
        if not key:
            blank_keys += 1
            continue
        filled = sum(1 for v in r.values() if (v or "").strip())
        if key not in by_key or filled > by_key[key][0]:
            by_key[key] = (filled, r)

    dup_keys = len(rows) - blank_keys - len(by_key)

    for key, (_filled, r) in by_key.items():
        dt = parse_dt(r[c_dt])
        scheme = "dash" if "-" in key else ("7digit" if len(key) == 7 else "other")
        st_raw = (r[c_st] or "").strip()
        st = canon_station(st_raw)
        review[st_raw] = st
        desc = (r[c_td] or "").strip()
        tc = (r[c_tc] or "").strip()
        ma = (r[c_ma] or "").strip()
        fam, sub = classify(tc, desc, ma)
        types[tc] = (desc, fam, sub)
        con.execute(
            "INSERT INTO fact_incident (incident_key, incident_no_scheme, incident_dt, year,"
            " station_code, station_raw, zone_district, street_name, street_number,"
            " type_code, type_desc, incident_class, incident_subtype, mutual_aid_given,"
            " city, latitude, longitude, alarm_to_arrival_min, alarm_to_cleared_min,"
            " alarm_to_controlled_min, psap_to_alarm_min, is_actual)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                key, scheme,
                dt.isoformat(sep=" ") if dt else None,
                dt.year if dt else None,
                st, st_raw or None,
                (r[c_zone] or "").strip() or None,
                (r[c_street] or "").strip() or None,
                (r[c_streetno] or "").strip() or None,
                tc or None, desc or None, fam, sub,
                ma or None,
                (r[c_city] or "").strip() or None,
                num(r[c_lat]), num(r[c_lon]),
                num(r[c_aa]), num(r[c_ac]), num(r[c_act]), num(r[c_psap]),
                1 if dt and dt.strftime("%Y-%m-%d") <= "2026-10-31" else 0,
            ),
        )
        n_ok += 1

    for tc, (desc, fam, sub) in types.items():
        con.execute("INSERT OR REPLACE INTO dim_incident_type VALUES (?,?,?,?)",
                    (tc or "", desc, fam, sub))

    # ---------------------------------------------------------------- unit rows
    with open(os.path.join(RAW, "elite_apparatus_fact.csv"), newline="", encoding="utf-8", errors="replace") as f:
        urows = list(csv.DictReader(f))
    u_num = col(urows[0], "Incident Number")
    u_aid = col(urows[0], "Apparatus Resource ID")
    u_aty = col(urows[0], "Apparatus Resource Type")
    u_dsp = col(urows[0], "Dispatch Date Time")
    u_arr = col(urows[0], "Arrival Date Time")
    u_clr = col(urows[0], "Clear Date Time")
    u_ins = col(urows[0], "In Service Date Time")
    u_dta = col(urows[0], "Dispatch To Arrival In Minutes")
    u_atc = col(urows[0], "Arrived To Cleared In Minutes")
    u_ats = col(urows[0], "Arrived To In Service In Minutes")
    u_td = col(urows[0], "Basic Incident Type (")

    n_units = 0
    for i, r in enumerate(urows, 1):
        key = (r[u_num] or "").strip()
        raw_id = (r[u_aid] or "").strip()
        code = canon_apparatus(raw_id)
        if code:
            apt_raw.setdefault(code, set()).add(raw_id)
        d = parse_dt(r[u_dsp])
        c = parse_dt(r[u_clr])
        hours = round((c - d).total_seconds() / 3600, 2) if d and c and c >= d else None
        fam, _ = classify(None, (r[u_td] or "").strip())
        con.execute(
            "INSERT OR REPLACE INTO fact_unit_response VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                i, key or None, code, raw_id or None, (r[u_aty] or "").strip() or None,
                d.isoformat(sep=" ") if d else None,
                (parse_dt(r[u_arr]) or None) and parse_dt(r[u_arr]).isoformat(sep=" "),
                c.isoformat(sep=" ") if c else None,
                (parse_dt(r[u_ins]) or None) and parse_dt(r[u_ins]).isoformat(sep=" "),
                num(r[u_dta]), num(r[u_atc]), num(r[u_ats]), hours, fam,
            ),
        )
        n_units += 1

    for code, spellings in apt_raw.items():
        con.execute("INSERT OR REPLACE INTO dim_apparatus VALUES (?,?)",
                    (code, ", ".join(sorted(spellings))))

    con.commit()

    # ---------------------------------------------------------------- review file
    with open(REVIEW, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["raw_value", "canonical", "status"])
        for raw, canon in sorted(review.items(), key=lambda x: (x[1] or "zzz", x[0])):
            w.writerow([raw, canon or "", "mapped" if canon else "UNMAPPED"])
        for code, spellings in sorted(apt_raw.items()):
            w.writerow([f"[apparatus] {', '.join(sorted(spellings))}", code, "mapped"])

    print(f"warehouse: {DB}")
    print(f"  source rows {len(rows)} -> {len(by_key)} incidents "
          f"(blank-key rows {blank_keys}, duplicate rows {dup_keys})")
    for t in ("fact_incident", "fact_unit_response", "dim_station", "dim_apparatus", "dim_incident_type"):
        print(f"  {t:22} {con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]:>6} rows")
    unmapped = [k for k, v in review.items() if not v and k]
    print(f"  unmapped stations     {len(unmapped):>6}  {unmapped[:5]}")
    con.close()


if __name__ == "__main__":
    main()
