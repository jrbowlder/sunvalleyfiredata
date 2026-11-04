#!/usr/bin/env python3
"""Analysis step 1: demand, response performance, and burden, 2020-2026.

Applies the two-basket rule from docs/SCHEMA.md:

  performance basket  in-scope incidents only -
                      no mutual aid given, arrival <= 25 min, on scene <= 4 h
  burden basket       everything excluded, counted as unit-hours consumed

Every number is printed with its denominator so it can be quoted safely.

Reads warehouse.db; set SVFD_DB to point at a different build (e.g. the synthetic one).
"""
from __future__ import annotations

import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.abspath(os.environ.get("SVFD_DB") or os.path.join(ROOT, "warehouse.db"))
START_YEAR = 2020


def pct(values, q):
    if not values:
        return None
    v = sorted(values)
    return v[min(len(v) - 1, int(len(v) * q))]


def main():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    print("=" * 74)
    print("DEMAND, 2020-2026 (actual records only)")
    print("=" * 74)
    rows = con.execute("""
        SELECT year,
               COUNT(*) AS total,
               SUM(CASE WHEN mutual_aid_given IS NOT NULL AND mutual_aid_given <> '' THEN 1 ELSE 0 END) AS mutual_aid,
               SUM(CASE WHEN incident_class = 'Wildland' THEN 1 ELSE 0 END) AS wildland,
               SUM(CASE WHEN alarm_to_arrival_min > 25 THEN 1 ELSE 0 END) AS slow_arrival
        FROM fact_incident
        WHERE is_actual = 1 AND year >= ?
        GROUP BY year ORDER BY year
    """, (START_YEAR,)).fetchall()

    print(f"{'year':<6}{'total':>7}{'mutual aid':>12}{'wildland':>10}{'arrival>25m':>13}")
    for r in rows:
        print(f"{r['year']:<6}{r['total']:>7}{r['mutual_aid'] or 0:>12}{r['wildland'] or 0:>10}{r['slow_arrival'] or 0:>13}")

    # in-scope basket
    in_scope = """
        FROM fact_incident
        WHERE is_actual = 1 AND year >= ?
          AND (mutual_aid_given IS NULL OR mutual_aid_given = '')
          AND incident_class <> 'Wildland'
          AND (alarm_to_arrival_min IS NULL OR alarm_to_arrival_min <= 25)
    """
    print("\n" + "=" * 74)
    print("RESPONSE PERFORMANCE - in-scope incidents, alarm -> arrival (minutes)")
    print("=" * 74)
    print(f"{'year':<6}{'n':>6}{'p50':>7}{'p90':>7}{'p95':>7}{'mean':>7}")
    for year in range(START_YEAR, 2027):
        vals = [r[0] for r in con.execute(
            "SELECT alarm_to_arrival_min " + in_scope +
            " AND alarm_to_arrival_min IS NOT NULL AND year = ?", (START_YEAR, year))]
        if not vals:
            continue
        mean = sum(vals) / len(vals)
        print(f"{year:<6}{len(vals):>6}{pct(vals,.5):>7.1f}{pct(vals,.9):>7.1f}"
              f"{pct(vals,.95):>7.1f}{mean:>7.1f}")

    print("\n" + "=" * 74)
    print("TIME ON SCENE - unit grain, arrived -> cleared (minutes)")
    print("=" * 74)
    print(f"{'year':<6}{'units':>7}{'p50':>7}{'p90':>7}{'p99':>7}")
    for year in range(START_YEAR, 2027):
        vals = [r[0] for r in con.execute("""
            SELECT arrived_to_cleared_min
            FROM fact_unit_response
            WHERE arrived_to_cleared_min IS NOT NULL
              AND arrived_to_cleared_min BETWEEN 0 AND 240
              AND incident_key IN (SELECT incident_key FROM fact_incident WHERE year = ?)
        """, (year,))]
        if not vals:
            continue
        print(f"{year:<6}{len(vals):>7}{pct(vals,.5):>7.1f}{pct(vals,.9):>7.1f}{pct(vals,.99):>7.1f}")

    print("\n" + "=" * 74)
    print("BURDEN - unit-hours committed per year (all unit rows)")
    print("=" * 74)
    print(f"{'year':<6}{'units':>7}{'unit-hours':>12}{'hours/day':>11}")
    for year in range(START_YEAR, 2027):
        r = con.execute("""
            SELECT COUNT(*) n, ROUND(SUM(unit_hours),1) h
            FROM fact_unit_response
            WHERE unit_hours IS NOT NULL AND unit_hours BETWEEN 0 AND 48
              AND incident_key IN (SELECT incident_key FROM fact_incident WHERE year = ?)
        """, (year,)).fetchone()
        if not r or not r["n"]:
            continue
        days = 366 if year % 4 == 0 else 365
        print(f"{year:<6}{r['n']:>7}{r['h']:>12}{r['h']/days:>11.2f}")

    con.close()


if __name__ == "__main__":
    main()
