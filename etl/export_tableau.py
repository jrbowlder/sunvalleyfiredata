#!/usr/bin/env python3
"""Export Tableau-ready extracts from a warehouse.

Tableau cannot read SQLite, so the workbook is fed flat CSVs. Everything here is derived
with the same definitions the dashboard uses (two baskets, nearest-rank percentiles, the
station-coverage rule), so the portfolio viz and the app cannot disagree.

  python3 etl/export_tableau.py [--db PATH] [--out DIR]

Writes, under data/tableau/ by default:

  incidents.csv          one row per incident - demand and call-type views
  response_monthly.csv   in-scope response percentiles per month (pre-computed)
  coverage_monthly.csv   staffed days and paramedic days per station-month
  locations.csv          geocoded incidents for a map sheet

Percentiles are pre-computed on purpose: Tableau's PERCENTILE is a table calculation whose
answer depends on the view's level of detail, so a reader who changes a filter would get a
different number than the count next to it. Pre-computing keeps one definition.
"""
from __future__ import annotations

import argparse
import csv
import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESPONSE_CAP = 25


def pct(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile, the convention analysis/01 uses."""
    v = sorted(values)
    if not v:
        return None
    return round(v[min(len(v) - 1, int(len(v) * q))], 1)


def write(path: str, header: list[str], rows) -> int:
    n = 0
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)
            n += 1
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(ROOT, "data", "synthetic", "warehouse.db"))
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "tableau"))
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    # ---------------------------------------------------------------- incidents
    rows = con.execute("""
        SELECT incident_uid, incident_dt, year, station_code, incident_class,
               incident_subtype, mutual_aid_given, org_code, schema_era,
               alarm_to_arrival_min, geo_lat, geo_lon, geo_source, street_name
        FROM fact_incident
        WHERE is_actual = 1
        ORDER BY incident_dt
    """).fetchall()

    out = []
    for r in rows:
        dt = (r["incident_dt"] or "")[:19]
        day = dt[:10]
        month = f"{dt[:7]}-01" if len(dt) >= 7 else ""
        in_scope = (not (r["mutual_aid_given"] or "").strip()
                    and r["incident_class"] != "Wildland"
                    and (r["alarm_to_arrival_min"] is None
                         or r["alarm_to_arrival_min"] <= RESPONSE_CAP))
        out.append([
            r["incident_uid"], dt, day, month, r["year"],
            r["station_code"], r["incident_class"], r["incident_subtype"],
            "yes" if (r["mutual_aid_given"] or "").strip() else "no",
            r["org_code"] or "unresolved", r["schema_era"],
            "in scope" if in_scope else "burden",
            r["alarm_to_arrival_min"],
        ])
    n = write(os.path.join(args.out, "incidents.csv"),
              ["incident_uid", "incident_datetime", "incident_date", "month_start", "year",
               "station",
               "family", "subtype", "mutual_aid_given", "jurisdiction", "era", "basket",
               "response_min"], out)

    # ---------------------------------------------------- response by month (in scope)
    buckets: dict[str, list[float]] = {}
    for r in rows:
        if (r["mutual_aid_given"] or "").strip():
            continue
        if r["incident_class"] == "Wildland":
            continue
        t = r["alarm_to_arrival_min"]
        if t is None or t > RESPONSE_CAP:
            continue
        buckets.setdefault((r["incident_dt"] or "")[:7], []).append(t)
    resp = [[f"{m}-01", len(v), pct(v, .5), pct(v, .9), pct(v, .95)]
            for m, v in sorted(buckets.items()) if m]
    n2 = write(os.path.join(args.out, "response_monthly.csv"),
               ["month_start", "timed_incidents", "response_p50", "response_p90",
               "response_p95"],
               resp)

    # ------------------------------------------------------- coverage by station-month
    cov = con.execute("""
        SELECT substr(work_date, 1, 7) AS month, station_code,
               COUNT(DISTINCT work_date) AS days_with_rows,
               SUM(CASE WHEN headcount > 0 THEN 1 ELSE 0 END) AS staffed_days,
               SUM(CASE WHEN has_paramedic = 1 THEN 1 ELSE 0 END) AS medic_days
        FROM v_station_day
        WHERE is_actual = 1
        GROUP BY month, station_code
        ORDER BY month, station_code
    """).fetchall()
    n3 = write(os.path.join(args.out, "coverage_monthly.csv"),
               ["month_start", "station", "station_days", "staffed_days", "medic_days"],
               [[f"{r['month']}-01", r["station_code"], r["days_with_rows"],
                 r["staffed_days"],
                 r["medic_days"]] for r in cov])

    # ------------------------------------------------------------------- locations
    locs = [[r["incident_uid"], r["geo_lat"], r["geo_lon"], r["geo_source"],
             r["incident_class"], r["station_code"], r["year"]]
            for r in rows if r["geo_lat"] is not None]
    n4 = write(os.path.join(args.out, "locations.csv"),
               ["incident_uid", "latitude", "longitude", "geo_source", "family", "station",
                "year"], locs)

    print(f"  incidents.csv         {n:>6} rows")
    print(f"  response_monthly.csv  {n2:>6} rows")
    print(f"  coverage_monthly.csv  {n3:>6} rows")
    print(f"  locations.csv         {n4:>6} rows")
    print(f"  -> {args.out}")
    con.close()


if __name__ == "__main__":
    main()
