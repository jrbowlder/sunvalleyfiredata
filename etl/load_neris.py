#!/usr/bin/env python3
"""Load the NERIS era (2026+) into the same fact_incident table.

Runs after etl/load_elite.py, which builds the NFIRS era (2011-2025) and the
dimensions. This module is additive: it extends fact_incident with the columns
the NERIS era needs, marks existing rows as the NFIRS era, then maps NERIS
fields onto the same shape.

Mapping rules (see docs/SCHEMA.md):

  incident_key   fiDispatch.03
  incident_dt    Dispatch Alarm -> Incident Alarm -> Incident Date Time
                 (first populated wins; the winner is recorded in alarm_source)
  arrival_dt     Dispatch Arrival -> Incident Arrival
  cleared_dt     Dispatch Incident Clear
  in_service_dt  Incident In Service
  station        Incident Primary Station -> canon_station()
  type           Dispatch Incident Code / Incident Type
  mutual aid     Incident Aid Given Count > 0
  org_code       SVFD / NBCFD from the incident-number prefix, else NULL+review

Numbers arrive locale-formatted ('61,055'), so commas are stripped before parse.
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
CLEAN = os.path.abspath(os.environ.get("SVFD_CLEAN")
                        or os.path.join(os.path.dirname(RAW), "clean"))
REVIEW = os.path.join(CLEAN, "neris_review.csv")
ASOF = "2026-10-31"

NEW_COLUMNS = [
    ("schema_era", "TEXT"),
    ("org_code", "TEXT"),
    ("org_source", "TEXT"),
    ("alarm_source", "TEXT"),
    ("arrival_dt", "TEXT"),
    ("cleared_dt", "TEXT"),
    ("in_service_dt", "TEXT"),
    ("incident_subtype", "TEXT"),
]

# Jurisdiction cannot be read from the incident number: crews freely mix the
# department's 26-NNN series with the dispatch-generated number, so the format
# says nothing about which agency ran the call. Only an explicit SVFD/NBCFD
# prefix is authoritative; otherwise the location zone is used and flagged.
ZONE_ORG = {
    # BCSO zones are sheriff patrol areas, not fire jurisdictions - BCSO attaches to
    # calls anywhere, so they carry no jurisdiction signal. Only a city zone name is
    # usable, and only as an inference.
    "sun valley": "SVFD",
}

STATION_MAP = {
    "elkhorn station": "ELK",
    "elkhorn station - hq": "ELK",
    "elkhorn": "ELK",
    "station 2": "GH",
    "greenhorn": "GH",
    "greenhorn station": "GH",
    "station 3": "GB",
    "griffin butte": "GB",
    "griffin butte station": "GB",
}

# call-type classification lives in etl/taxonomy.py (NFIRS code first, keywords only
# as a fallback). This export has no usable type field, so rows land as 'Untyped'.


def canon_station(raw):
    return STATION_MAP.get((raw or "").strip().lower()) if raw else None


def parse_dt(v):
    if not v or not str(v).strip():
        return None
    v = str(v).strip()
    for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    return None


def num(v):
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def minutes(a, b):
    if a and b and b >= a:
        return round((b - a).total_seconds() / 60, 1)
    return None


def iso(dt):
    return dt.isoformat(sep=" ") if dt else None


def c(row, *frags):
    for k in row:
        if all(f.lower() in k.lower() for f in frags):
            return k
    return None


def exact(row, name):
    for k in row:
        if k.strip().lower() == name.strip().lower():
            return k
    return None


def org_from_number(incident_no):
    n = (incident_no or "").upper()
    for tag in ("SVFD", "NBCFD"):
        if n.startswith(tag):
            return tag
    return None


def ensure_columns(con):
    have = {r[1] for r in con.execute("PRAGMA table_info(fact_incident)")}
    added = []
    for name, typ in NEW_COLUMNS:
        if name not in have:
            con.execute(f"ALTER TABLE fact_incident ADD COLUMN {name} {typ}")
            added.append(name)
    # everything loaded by load_elite.py is NFIRS era
    con.execute("UPDATE fact_incident SET schema_era='nfirs' WHERE schema_era IS NULL")
    return added


def main():
    if not os.path.exists(DB):
        raise SystemExit(f"no warehouse at {DB} - run etl/build.py first")
    path = os.path.join(RAW, "elite_neris_incident_fact.csv")
    if not os.path.exists(path):
        raise SystemExit(f"no NERIS pull in {RAW}/")

    con = sqlite3.connect(DB)
    added = ensure_columns(con)

    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        rows = list(csv.DictReader(f))

    k_num = c(rows[0], "fiDispatch.03")
    k_type = exact(rows[0], "Incident Type")
    k_aid_g = exact(rows[0], "Incident Aid Given Count")
    k_code = c(rows[0], "fiDispatch.05")
    k_city = c(rows[0], "fiDispatch.11")
    k_station = c(rows[0], "itfiIncident.013")
    k_district = c(rows[0], "itfiIncident.014")
    k_zone = c(rows[0], "itfiIncident.008")
    k_d_alarm = c(rows[0], "itfiDispatch.001")
    k_d_arr = c(rows[0], "itfiDispatch.002")
    k_d_clear = c(rows[0], "fiDispatch.01")
    k_i_alarm = c(rows[0], "itfiIncident.002")
    k_i_arr = c(rows[0], "itfiIncident.003")
    k_i_date = c(rows[0], "itfiIncident.004")
    k_i_service = c(rows[0], "itfiIncident.005")

    loaded = 0
    no_alarm = 0
    unresolved_org = []
    station_review = {}
    type_review = {}

    # 7 incident numbers appear twice in NERIS, a few on different days (miscopied
    # numbers). Keep the richest row per key and log the rest - double-counting demand
    # is worse than dropping one, but it must be visible.
    by_key = {}
    duplicate_keys = []
    blank_keys = 0
    for r in rows:
        key = (r.get(k_num) or "").strip()
        if not key:
            blank_keys += 1
            continue
        filled = sum(1 for v in r.values() if (v or "").strip())
        if key in by_key:
            duplicate_keys.append(key)
            if filled > by_key[key][0]:
                by_key[key] = (filled, r)
        else:
            by_key[key] = (filled, r)

    for key, (_filled, r) in by_key.items():

        alarm, alarm_src = None, None
        for col_key, label in ((k_d_alarm, "dispatch.alarm"),
                               (k_i_alarm, "incident.alarm"),
                               (k_i_date, "incident.date")):
            dt = parse_dt(r.get(col_key)) if col_key else None
            if dt:
                alarm, alarm_src = dt, label
                break
        if not alarm:
            no_alarm += 1

        arrival = parse_dt(r.get(k_d_arr)) or parse_dt(r.get(k_i_arr))
        cleared = parse_dt(r.get(k_d_clear))
        in_service = parse_dt(r.get(k_i_service))

        st_raw = (r.get(k_station) or "").strip()
        st = canon_station(st_raw)
        station_review[st_raw] = st

        org = org_from_number(key)
        org_source = "prefix" if org else None
        zone = (r.get(k_zone) or "").strip()
        if not org and zone:
            inferred = ZONE_ORG.get(zone.lower())
            if inferred:
                org, org_source = inferred, "zone_inferred"
        if not org:
            unresolved_org.append(key)

        aid = num(r.get(k_aid_g)) if k_aid_g else None
        ma = "given" if (aid or 0) > 0 else ""
        desc = (r.get(k_type) or "").strip()
        code = (r.get(k_code) or "").strip()
        type_review[code] = desc

        con.execute(
            """INSERT INTO fact_incident
               (incident_key, schema_era, org_code, org_source, incident_no_scheme, incident_dt,
                alarm_source, year, station_code, station_raw, zone_district,
                type_code, type_desc, incident_class, incident_subtype,
                mutual_aid_given, city, latitude, longitude, arrival_dt, cleared_dt,
                in_service_dt, alarm_to_arrival_min, alarm_to_cleared_min,
                alarm_to_controlled_min, psap_to_alarm_min, is_actual)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (key, "neris", org, org_source,
             "prefixed" if org_from_number(key) else ("dash" if "-" in key else "other"),
             iso(alarm), alarm_src, alarm.year if alarm else None,
             st, st_raw or None, zone or None,
             code or None, desc or None, *classify(code, desc, ma),
             ma or None, (r.get(k_city) or "").strip() or None,
             None, None,
            iso(arrival), iso(cleared), iso(in_service),
             # 'incident.date' is a DATE, not a time - it parses to midnight, which would
             # fabricate a multi-hour response. Use it only to bucket the incident, never
             # to compute an interval.
             (minutes(alarm, arrival) if alarm_src != "incident.date" else None),
             (minutes(alarm, cleared) if alarm_src != "incident.date" else None),
             None, None,
             1 if alarm and alarm.strftime("%Y-%m-%d") <= ASOF else 0),
        )
        loaded += 1

    for code, desc in type_review.items():
        if code:
            con.execute("INSERT OR REPLACE INTO dim_incident_type VALUES (?,?,?,?)",
                        (code, desc, *classify(code, desc)))
    con.commit()

    os.makedirs(CLEAN, exist_ok=True)
    with open(REVIEW, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["raw_value", "canonical", "status", "field"])
        for raw, canon in sorted(station_review.items(), key=lambda x: (x[1] or "zzz", x[0])):
            w.writerow([raw, canon or "", "mapped" if canon else "UNMAPPED", "neris.station"])
        for key in unresolved_org:
            w.writerow([key, "", "UNRESOLVED ORG", "neris.incident"])

    print(f"  NERIS: {len(rows)} rows -> {loaded} incidents "
          f"(blank keys {blank_keys}, duplicate numbers {len(set(duplicate_keys))})")
    print(f"         columns added: {added or '(none, already present)'}")
    print(f"         no alarm time: {no_alarm}   org unresolved: {len(unresolved_org)}")
    print("         by alarm source:", dict(con.execute(
        "SELECT COALESCE(alarm_source,'(none)'), COUNT(*) FROM fact_incident "
        "WHERE schema_era='neris' GROUP BY 1").fetchall()))
    print("         by year:", dict(con.execute(
        "SELECT year, COUNT(*) FROM fact_incident WHERE schema_era='neris' "
        "GROUP BY 1 ORDER BY 1").fetchall()))
    con.close()


if __name__ == "__main__":
    main()
