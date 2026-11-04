#!/usr/bin/env python3
"""Analysis step 2: demand against coverage.

Joins incidents to the staffing calendar on date + station. Only 2025-01-01 onward
has real schedule data, so every comparison is restricted to that window and states
its denominators.

The question this answers: when a station is not staffed, what happens to the calls
attributed to it - do they get slower, and by how much?

Reads warehouse.db; set SVFD_DB to point at a different build (e.g. the synthetic one).
"""
from __future__ import annotations

import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.abspath(os.environ.get("SVFD_DB") or os.path.join(ROOT, "warehouse.db"))


def pct(vals, q):
    if not vals:
        return None
    v = sorted(vals)
    return v[min(len(v) - 1, int(len(v) * q))]


def main():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    # incidents attributed to a station, joined to that station's staffing that day
    base = """
    SELECT i.incident_uid, i.incident_dt, i.station_code, i.year,
           i.alarm_to_arrival_min, i.incident_class, i.type_desc,
           COALESCE(v.headcount, 0) AS headcount,
           COALESCE(v.has_paramedic, 0) AS has_paramedic,
           CASE WHEN v.work_date IS NULL THEN 0 ELSE 1 END AS station_staffed
    FROM fact_incident i
    LEFT JOIN v_station_day v
           ON v.work_date = substr(i.incident_dt, 1, 10)
          AND v.station_code = i.station_code
          AND v.is_actual = 1
    WHERE i.is_actual = 1
      AND i.incident_dt >= '2025-01-01'
      AND i.station_code IS NOT NULL
      AND (i.mutual_aid_given IS NULL OR i.mutual_aid_given = '')
      AND i.incident_class <> 'Wildland'
    """
    rows = con.execute(base).fetchall()
    print(f"joined incidents (2025-01-01+, station known): {len(rows)}")

    print("\n" + "=" * 72)
    print("RESPONSE BY STATION AND STAFFING STATE  (alarm -> arrival, minutes)")
    print("=" * 72)
    print(f"{'station':<9}{'staffed':<9}{'n':>5}{'p50':>7}{'p90':>7}{'with_time':>11}")
    for station in ("ELK", "GH", "GB"):
        for staffed in (1, 0):
            sub = [r for r in rows if r["station_code"] == station and r["station_staffed"] == staffed]
            times = [r["alarm_to_arrival_min"] for r in sub if r["alarm_to_arrival_min"] is not None]
            if not sub:
                continue
            print(f"{station:<9}{('yes' if staffed else 'no'):<9}{len(sub):>5}"
                  f"{(pct(times,.5) if times else 0):>7.1f}{(pct(times,.9) if times else 0):>7.1f}"
                  f"{len(times):>11}")

    print("\n" + "=" * 72)
    print("GREENHORN AND ELKHORN: YEAR OVER YEAR, staffed share and speed")
    print("=" * 72)
    print(f"{'station':<9}{'year':<6}{'calls':>6}{'staffed':>9}{'p50 staffed':>12}{'p50 unstaffed':>15}")
    for station in ("ELK", "GH"):
        for year in (2025, 2026):
            sub = [r for r in rows if r["station_code"] == station and r["year"] == year]
            st = [r["alarm_to_arrival_min"] for r in sub
                  if r["station_staffed"] and r["alarm_to_arrival_min"] is not None]
            un = [r["alarm_to_arrival_min"] for r in sub
                  if not r["station_staffed"] and r["alarm_to_arrival_min"] is not None]
            if not sub:
                continue
            share = sum(1 for r in sub if r["station_staffed"]) / len(sub)
            print(f"{station:<9}{year:<6}{len(sub):>6}{share*100:>8.0f}%"
                  f"{(pct(st,.5) if st else float('nan')):>12.1f}"
                  f"{(pct(un,.5) if un else float('nan')):>15.1f}")

    print("\n" + "=" * 72)
    print("PARAMEDIC ON DUTY AT ELKHORN -> response to medical calls")
    print("=" * 72)
    ems = re_ems()
    med = [r for r in rows if r["station_code"] == "ELK" and ems(r["type_desc"])]
    for flag in (1, 0):
        sub = [r for r in med if r["has_paramedic"] == flag]
        times = [r["alarm_to_arrival_min"] for r in sub if r["alarm_to_arrival_min"] is not None]
        if not sub:
            continue
        print(f"  paramedic on duty {'yes' if flag else 'no ':>3}: {len(sub):>4} calls, "
              f"{len(times):>4} timed, p50 {(pct(times,.5) if times else float('nan')):.1f} min, "
              f"p90 {(pct(times,.9) if times else float('nan')):.1f} min")

    print("\n" + "=" * 72)
    print("DAILY VOLUME BY CREW SIZE (all stations, 2025-01-01+)")
    print("=" * 72)
    daily = con.execute("""
        SELECT d.work_date AS day, d.station_code, d.headcount,
               (SELECT COUNT(*) FROM fact_incident i
                 WHERE substr(i.incident_dt,1,10) = d.work_date AND i.is_actual = 1) AS calls
        FROM v_station_day d
        WHERE d.is_actual = 1
    """).fetchall()
    buckets = {}
    for r in daily:
        b = min(r["headcount"], 4)
        buckets.setdefault(b, []).append(r["calls"])
    print(f"{'crew size':<12}{'station-days':>14}{'mean calls/day':>16}")
    for b in sorted(buckets):
        v = buckets[b]
        print(f"{b}{'+' if b == 4 else '':<11}{len(v):>14}{sum(v)/len(v):>16.2f}")

    con.close()


def re_ems():
    import re
    pat = re.compile(r"ems|medical|patient|sick|injur", re.I)
    return lambda desc: bool(pat.search(desc or ""))


if __name__ == "__main__":
    main()
