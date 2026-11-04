#!/usr/bin/env python3
"""Build the SVFD Operations dashboard in Metabase, mirroring app/app.py.

Layout is flow-based: cards are grouped by section and packed into the 24-column grid,
so resizing a card never needs hand-computed row numbers anywhere else.
"""
import json, urllib.request, urllib.error, uuid

import os

BASE = os.environ.get("MB_URL", "http://127.0.0.1:3030")
DB = int(os.environ.get("MB_SOURCE_DB", "3"))          # the warehouse.db connection in Metabase
COLL = int(os.environ.get("MB_COLLECTION", "5"))       # "Fire Data" collection id
KEY = open(os.environ.get("MB_API_KEY_FILE", os.path.expanduser("~/metabase/api-key.txt"))).read().strip()

START = "2020-01-01"
END_DEFAULT = "2026-10-31"
# Dashboard date filters ride on basic date variables. Compared on the date part, so SQLite's
# text timestamps never trip on time of day. Literal {{ }} braces: INC is a plain string, and
# interpolation into other f-strings leaves them alone.
DF = "{{start_date}}"
DT = "{{end_date}}"
INC = ("fact_incident WHERE is_actual = 1 AND substr(incident_dt,1,10) >= '2020-01-01' "
       f"AND substr(incident_dt,1,10) >= {DF} AND substr(incident_dt,1,10) <= {DT}")
DATE_TAGS = {
    "start_date": {"id": "sd1", "name": "start_date", "display-name": "From", "type": "date",
                   "required": False, "default": START},
    "end_date": {"id": "ed1", "name": "end_date", "display-name": "To", "type": "date",
                 "required": False, "default": END_DEFAULT},
}
# Dashboard filters. Metabase only exposes a single "Date range" widget for field filters, which
# break inside CTEs, so two single-date pickers mapped to basic date variables is the reliable pair.
PARAM_FROM = {"name": "From", "slug": "start_date", "id": str(uuid.uuid4()),
              "type": "date/single", "sectionId": "date", "default": START}
PARAM_TO = {"name": "To", "slug": "end_date", "id": str(uuid.uuid4()),
            "type": "date/single", "sectionId": "date", "default": END_DEFAULT}
PARAMS = [PARAM_FROM, PARAM_TO]
PERF = ("COALESCE(mutual_aid_given,'') = '' AND COALESCE(incident_class,'') <> 'Wildland' "
        "AND (alarm_to_arrival_min IS NULL OR alarm_to_arrival_min <= 25)")
UNITS = """WITH m AS (
  SELECT u.arrived_to_cleared_min, u.unit_hours, i.incident_dt, i.year, i.incident_uid,
         ROW_NUMBER() OVER (PARTITION BY u.unit_row_id
           ORDER BY CASE WHEN u.dispatch_dt IS NOT NULL
                          AND i.year = CAST(substr(u.dispatch_dt,1,4) AS INTEGER)
                         THEN 0 ELSE 1 END, i.incident_uid) AS rn
  FROM fact_unit_response u JOIN fact_incident i ON i.incident_key = u.incident_key
)"""
STATION = ("CASE station_code WHEN 'ELK' THEN 'Elkhorn (HQ)' WHEN 'GH' THEN 'Greenhorn' "
           "WHEN 'GB' THEN 'Griffin Butte' ELSE COALESCE(station_code,'Unknown') END")
ISTATION = STATION.replace("station_code", "i.station_code")

MIN2 = {"column_settings": {'["name","value"]': {"number_style": "decimal", "decimals": 2,
                                                "suffix": " min"}}}
MIN1 = {"column_settings": {'["name","value"]': {"number_style": "decimal", "decimals": 1,
                                                "suffix": " min"}}}
PERDAY = {"column_settings": {'["name","value"]': {"number_style": "decimal", "decimals": 2,
                                                   "suffix": " /day"}}}


def nr_scalar(col, p):
    return f"""WITH b AS (SELECT {col} AS v FROM {INC} AND {PERF} AND {col} IS NOT NULL AND {col} <= 25),
r AS (SELECT v, ROW_NUMBER() OVER (ORDER BY v) AS rn, COUNT(*) OVER () AS n FROM b)
SELECT v AS value FROM r WHERE rn = MIN(n-1, CAST(n*{p} AS INT)) + 1"""


def nr_group(p, alias, col="v"):
    return f"MAX(CASE WHEN rn = MIN(n-1, CAST(n*{p} AS INT)) + 1 THEN {col} END) AS {alias}"


def ranked(src_select, key_expr):
    return f"""WITH base AS ({src_select}),
ranked AS (SELECT {key_expr} AS k, v, ROW_NUMBER() OVER (PARTITION BY {key_expr} ORDER BY v) AS rn,
           COUNT(*) OVER (PARTITION BY {key_expr}) AS n FROM base WHERE v IS NOT NULL)"""


CARDS = []


def card(section, name, sql, display, sx, sy, viz=None, desc="", end_default=None):
    CARDS.append(dict(section=section, name=name, sql=sql, display=display, sx=sx, sy=sy,
                      viz=viz or {}, desc=desc, end_default=end_default))


# ------------------------------------------------------------------ KPIs (8 wide x 5 tall)
card("", "Incidents (2020+)", f"SELECT COUNT(*) AS value FROM {INC}", "scalar", 8, 5,
     desc="Every actual incident with an incident date on or after 1 January 2020. The window "
          "starts there because reporting practice changed in 2020: counts jump 2.19x from 2019 "
          "to 2020 with no real change in workload, so a trend spanning that boundary would be "
          "read as a data artifact.")
card("", "In-Scope Incidents", f"SELECT COUNT(*) AS value FROM {INC} AND {PERF}", "scalar", 8, 5,
     desc="The performance basket. An incident is in scope when no mutual aid was given, it is "
          "not a wildland deployment, and the first unit arrived within 25 minutes. Out-of-scope "
          "work is not bad data - it is reported separately as burden (unit-hours, exclusions), "
          "following NFPA 1710/1720, which treats it as workload rather than response "
          "performance.")
card("", "Response Time p50", nr_scalar("alarm_to_arrival_min", 0.5), "scalar", 8, 5, MIN2,
     desc="p50 = the median. Half of in-scope incidents had a first unit on scene at or under "
          "this many minutes; half took longer. Nearest-rank percentile on alarm-to-arrival "
          "time, so it matches the printed analysis rather than interpolating.")
card("", "Response Time p90", nr_scalar("alarm_to_arrival_min", 0.9), "scalar", 8, 5, MIN2,
     desc="p90 = 9 in 10. Ninety percent of in-scope incidents were at or under this many "
          "minutes; one in ten took longer. The tail is what residents experience on the worst "
          "day, which is why p90 is reported next to the median and never instead of it.")
card("", "Time on Scene p50",
     f"""WITH b AS (
  SELECT alarm_to_cleared_min - alarm_to_arrival_min AS v FROM {INC}
  AND alarm_to_arrival_min IS NOT NULL AND alarm_to_cleared_min IS NOT NULL
  AND alarm_to_cleared_min - alarm_to_arrival_min BETWEEN 0 AND 240),
r AS (SELECT v, ROW_NUMBER() OVER (ORDER BY v) AS rn, COUNT(*) OVER () AS n FROM b)
SELECT v AS value FROM r WHERE rn = MIN(n-1, CAST(n*0.5 AS INT)) + 1""", "scalar", 8, 5, MIN1,
     desc="Median minutes from first unit arrival to the last unit clearing. Incident grain: alarm "
          "to last unit cleared, minus alarm to first arrival, 4 h cap. Chosen over the "
          "per-apparatus measure because apparatus rows stop with the NFIRS era, so this is the "
          "only definition that covers 2026. On 2020-2025 the two agree closely: the per-apparatus "
          "median runs 0.8-3.0 minutes higher, so read this as the incident's duration after "
          "arrival rather than any single crew's time on scene.")
card("", "Calls per Day",
     f"""SELECT ROUND(COUNT(*) * 1.0 /
  (CAST(julianday(MAX(incident_dt)) - julianday(MIN(incident_dt)) AS INT) + 1), 2) AS value
FROM {INC}""", "scalar", 8, 5, PERDAY,
     desc="Incidents divided by calendar days in the window. Average load per day across SVFD "
          "and NBCFD combined.")

# --------------------------------------------------------------------------------- Demand
card("Demand", "Monthly demand", f"""WITH RECURSIVE months(m) AS (
  SELECT substr({DF},1,7)
  UNION ALL
  SELECT strftime('%Y-%m', date(m || '-01', '+1 month')) FROM months
  WHERE m < substr({DT},1,7)
),
labels AS (SELECT '01' AS n, 'Jan' AS mon UNION ALL SELECT '02','Feb' UNION ALL SELECT '03','Mar'
  UNION ALL SELECT '04','Apr' UNION ALL SELECT '05','May' UNION ALL SELECT '06','Jun'
  UNION ALL SELECT '07','Jul' UNION ALL SELECT '08','Aug' UNION ALL SELECT '09','Sep'
  UNION ALL SELECT '10','Oct' UNION ALL SELECT '11','Nov' UNION ALL SELECT '12','Dec'),
counts AS (
  SELECT substr(incident_dt,1,7) AS mo, COUNT(*) AS incidents
  FROM {INC} GROUP BY 1
)
SELECT l.mon || ' ' || substr(m.m,1,4) AS month, COALESCE(c.incidents, 0) AS incidents
FROM months m
LEFT JOIN labels l ON l.n = substr(m.m,6,2)
LEFT JOIN counts c ON c.mo = m.m
ORDER BY m.m""", "bar", 16, 8,
     {"graph.dimensions": ["month"], "graph.metrics": ["incidents"],
      "graph.x_axis.axis_enabled": "compact"},
     "Incidents per month across the selected range, including months with no incidents so the "
     "axis spans the whole window. The trailing months are under-counted: department reports are "
     "written after the fact and some stay outstanding for months, so read the tail as reporting "
     "lag, never as falling demand.")
card("Demand", "Incidents by year", f"SELECT CAST(year AS TEXT) AS year, COUNT(*) AS incidents FROM {INC} GROUP BY 1 ORDER BY 1",
     "bar", 8, 8, {"graph.dimensions": ["year"], "graph.metrics": ["incidents"]},
     "The final bar is a partial year (to 31 Oct 2026).")
card("Demand", "Call-type families", f"""SELECT COALESCE(incident_class,'Untyped') AS family, COUNT(*) AS incidents
FROM {INC} GROUP BY 1 ORDER BY 2 DESC""", "row", 10, 8,
     {"graph.dimensions": ["family"], "graph.metrics": ["incidents"],
      # row charts put the category axis on the vertical, so x_axis.* is the "family" label;
      # an empty string removes the title while keeping the tick labels
      "graph.x_axis.title_text": ""},
     "Families come from the NFIRS type code, not keyword matching. Mutual aid is a flag, not a "
     "type: a mutual-aid structure fire still counts as a structure fire.")
card("Demand", "Call types (all incidents)", f"""SELECT COALESCE(incident_class,'Untyped') AS family,
  COALESCE(incident_subtype,'-') AS subtype, COUNT(*) AS incidents,
  ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 1) AS share_pct
FROM {INC} GROUP BY 1,2 ORDER BY 1, 3 DESC""", "table", 14, 8, {},
     "All incidents in range, before the in-scope basket is applied.")

# ------------------------------------------------------------------------------- Response
card("Response", "Response time p50/p90 by month",
     f"""{ranked(f"SELECT alarm_to_arrival_min AS v, substr(incident_dt,1,7) AS mo FROM {INC} AND {PERF} AND alarm_to_arrival_min <= 25", "mo")}
SELECT k AS month, n, {nr_group(0.5, 'p50')}, {nr_group(0.9, 'p90')}
FROM ranked GROUP BY k, n HAVING n >= 3 ORDER BY k""", "line", 16, 8,
     {"graph.dimensions": ["month"], "graph.metrics": ["p50", "p90"]},
     "Minutes, alarm to first unit arrival, in-scope incidents only. Months with fewer than 3 "
     "timed incidents are dropped rather than plotted as a spike.")
card("Response", "Response-time distribution",
     f"""SELECT CAST(alarm_to_arrival_min AS INT) AS minutes, COUNT(*) AS incidents
FROM {INC} AND {PERF} AND alarm_to_arrival_min IS NOT NULL GROUP BY 1 ORDER BY 1""",
     "bar", 8, 8, {"graph.dimensions": ["minutes"], "graph.metrics": ["incidents"]},
     "Every in-scope incident with a usable arrival time, in 1-minute bins.")
card("Response", "Percentiles by year",
     f"""{ranked(f"SELECT alarm_to_arrival_min AS v, year FROM {INC} AND {PERF} AND alarm_to_arrival_min <= 25", "year")}
SELECT CAST(k AS TEXT) AS year, n, {nr_group(0.5, 'p50')}, {nr_group(0.9, 'p90')}, MAX(v) AS worst
FROM ranked GROUP BY k, n ORDER BY k""", "bar", 12, 8,
     {"graph.dimensions": ["year"], "graph.metrics": ["p50", "p90"]},
     "Minutes. Response-time completeness differs by reporting system (about 89% in the NFIRS "
     "era versus 49% in the NERIS era), so a year's percentile can rest on half as many records "
     "as its neighbour.")
card("Response", "Time on scene p50 by month",
     f"""WITH base AS (
  SELECT substr(incident_dt,1,7) AS mo, alarm_to_cleared_min - alarm_to_arrival_min AS v
  FROM {INC}
  AND alarm_to_arrival_min IS NOT NULL AND alarm_to_cleared_min IS NOT NULL
  AND alarm_to_cleared_min - alarm_to_arrival_min BETWEEN 0 AND 240
),
ranked AS (SELECT mo AS k, v, ROW_NUMBER() OVER (PARTITION BY mo ORDER BY v) AS rn,
           COUNT(*) OVER (PARTITION BY mo) AS n FROM base)
SELECT k AS month, n, {nr_group(0.5, 'p50')}
FROM ranked GROUP BY k, n HAVING n >= 10 ORDER BY k""", "line", 12, 8,
     {"graph.dimensions": ["month"], "graph.metrics": ["p50"],
      # a single-metric chart labels the y axis with the column name; name the unit instead
      "graph.y_axis.title_text": "minutes"},
     "Minutes, first unit arrival to last unit clearing, incident grain (alarm to cleared minus "
     "alarm to arrival, 4 h cap), so the series runs the whole way to Oct 2026. The per-apparatus "
     "measure stops with the NFIRS era: 2026 has apparatus rows on 26 of 63 January incidents, 3 "
     "of 111 in August, none in between. On 2020-2025 the two agree within 0.8-3.0 minutes, the "
     "apparatus median being slightly higher. Months with fewer than 10 derivable incidents are "
     "dropped, so Feb-Apr 2026 are gaps where the reporting transition left too little to read.")

# ------------------------------------------------------------------------------- Stations
card("Stations", "Call volume by station", f"""SELECT {STATION} AS station, COUNT(*) AS calls FROM {INC}
AND station_code IS NOT NULL GROUP BY 1 ORDER BY 2 DESC""", "bar", 8, 8,
     {"graph.dimensions": ["station"], "graph.metrics": ["calls"]},
     "Incidents attributed to the reporting station. This is not the station that responded - no "
     "field records that. 42% of incidents carry no station at all and are excluded here.")
card("Stations", "Response time by station (in-scope)",
     f"""{ranked(f"SELECT alarm_to_arrival_min AS v, {STATION} AS st FROM {INC} AND {PERF} AND station_code IS NOT NULL AND alarm_to_arrival_min <= 25", "st")}
SELECT k AS station, n AS timed, {nr_group(0.5, 'p50')}, {nr_group(0.9, 'p90')}
FROM ranked GROUP BY k, n ORDER BY k""", "bar", 16, 8,
     {"graph.dimensions": ["station"], "graph.metrics": ["p50", "p90"]},
     "Minutes. Volume counts all work; the percentiles use the same in-scope basket as the KPI "
     "row, so a station's p90 cannot be inflated by mutual-aid and wildland runs. 'timed' is how "
     "many incidents had a usable arrival time.")
card("Stations", "Response time by station and staffing state (2025+)",
     f"""{ranked(f'''SELECT i.alarm_to_arrival_min AS v, CASE WHEN COALESCE(s.headcount,0) > 0 THEN 'staffed' ELSE 'not staffed' END || ' - ' || {ISTATION} AS st
FROM fact_incident i
LEFT JOIN v_station_day s ON s.station_code = i.station_code
  AND s.work_date = substr(i.incident_dt,1,10) AND s.is_actual = 1
WHERE i.is_actual = 1 AND i.incident_dt >= '2025-01-01' AND i.station_code IS NOT NULL
  AND substr(i.incident_dt,1,10) >= {DF} AND substr(i.incident_dt,1,10) <= {DT}
  AND {PERF} AND i.alarm_to_arrival_min <= 25''', "st")}
SELECT k AS station_state, n, {nr_group(0.5, 'p50')}
FROM ranked GROUP BY k, n ORDER BY p50 DESC""", "bar", 16, 8,
     {"graph.dimensions": ["station_state"], "graph.metrics": ["p50"],
      # four long "state - station" labels get thinned to every other one; rotate so all show
      "graph.x_axis.axis_enabled": "rotate-45"},
     "Minutes. Staffing data starts Dec 2024, so this covers only 2025 onward. 'Not staffed' "
     "includes days with no calendar row at all. Griffin Butte is unstaffed - its handful of "
     "records are reporting locations, not workload signals: its bar here rests on n=3, which is "
     "a hypothesis, not a finding.")

# ------------------------------------------------------------------------------------ Map
card("Map", "Incident map — one point per ~150 m cell",
     f"""WITH pts AS (
  SELECT ROUND(geo_lat/0.0015)*0.0015 AS lat, ROUND(geo_lon/0.0015)*0.0015 AS lon,
         COALESCE(incident_class,'Untyped') AS cls
  FROM {INC} AND geo_lat IS NOT NULL AND geo_lon IS NOT NULL
),
agg AS (SELECT lat, lon, cls, COUNT(*) AS n FROM pts GROUP BY 1,2,3),
ranked AS (SELECT lat, lon, cls, n,
           ROW_NUMBER() OVER (PARTITION BY lat, lon ORDER BY n DESC, cls) AS rn FROM agg)
SELECT lat AS cell_lat, lon AS cell_lon, cls AS call_type, n AS incidents
FROM ranked WHERE rn = 1 ORDER BY incidents DESC LIMIT 2000""", "map", 24, 12,
     {"map.type": "pin", "map.latitude_column": "cell_lat", "map.longitude_column": "cell_lon",
      "map.pin_type": "markers", "graph.dimensions": ["call_type"]},
     "One point per ~150 m cell, using Metabase's standard blue pins. Metabase OSS does not let a "
     "pin map colour points by category - the setting is stored and silently ignored, no legend "
     "appears - so this map carries location, not call type; the type split lives in the Call-type "
     "families chart. Raw incidents cannot be drawn either: the cap is 1,000 markers and 3,315 "
     "incidents are geocoded, so narrowing the date filter is how you get individual points. "
     "Station markers are absent: dim_station has no coordinates, and inferring them from incident "
     "centroids would be an attribution guess. Geocoding is offline against OpenStreetMap; "
     "street-centroid points bunch mid-street, so a long road reads as one cell. If the overlapping "
     "pins get in the way, switching Pin type to tiles in the card's visualization settings shrinks "
     "them to small squares.")
card("Coverage", "Coverage by station (2025+)", f"""WITH s AS (
  SELECT work_date, station_code, headcount, has_paramedic FROM v_station_day
  WHERE is_actual = 1 AND work_date >= {DF} AND work_date <= {DT}),
w AS (SELECT COUNT(DISTINCT work_date) AS d FROM s)
SELECT {STATION} AS station,
  ROUND(SUM(CASE WHEN headcount > 0 THEN 1 ELSE 0 END) * 100.0 / (SELECT d FROM w), 1) AS staffed_pct,
  CASE WHEN MIN(station_code) = 'ELK'
       THEN ROUND(SUM(CASE WHEN has_paramedic = 1 THEN 1 ELSE 0 END) * 100.0 / (SELECT d FROM w), 1)
  END AS paramedic_pct
FROM s GROUP BY 1 ORDER BY 3 DESC""", "bar", 12, 8,
     {"graph.dimensions": ["station"], "graph.metrics": ["staffed_pct", "paramedic_pct"]},
     "Percent of calendar days in range, not percent of staffed days. The denominator is every "
     "date the calendar covers, otherwise an unstaffed station looks fully covered by virtue of "
     "having few rows. Actual staffing only - planned shifts after the pull date are excluded. "
     "Paramedic coverage is shown for Elkhorn only, because medic coverage is an Elkhorn "
     "commitment; Greenhorn has no bar rather than a misleading one.")
card("Coverage", "Paramedic coverage by month", f"""SELECT substr(work_date,1,7) AS month, {STATION} AS station,
  ROUND(SUM(has_paramedic) * 100.0 / COUNT(*), 1) AS paramedic_pct
FROM v_station_day WHERE is_actual = 1 AND station_code = 'ELK'
  AND work_date >= {DF} AND work_date <= {DT}
GROUP BY 1,2 ORDER BY 1""", "line", 12, 8,
     {"graph.dimensions": ["month"], "graph.metrics": ["paramedic_pct"],
      "graph.y_axis.title_text": "% of days"},
     "Elkhorn only, because medic coverage is an Elkhorn commitment - a medic-certified member "
     "assigned to Greenhorn is a person's credential, not a coverage promise, so that line would "
     "read as service the department does not run. Elkhorn's share of calendar days in the month "
     "with a paramedic on duty; uses confirmed start dates only (certificate or "
     "department-confirmed), so three members with estimated dates are excluded and this is the "
     "lower bound.")
# --------------------------------------------------------------------------------- Burden
card("Burden", "Work split - performance vs burden", f"""SELECT 'Performance' AS basket, COUNT(*) AS incidents
FROM {INC} AND {PERF}
UNION ALL SELECT 'Burden', COUNT(*) FROM {INC} AND NOT ({PERF})""", "pie", 8, 8,
     {"pie.dimension": "basket", "pie.metric": "incidents"},
     "Performance basket versus out-of-scope work. Out-of-scope work is real work, reported as "
     "burden rather than scrubbed from the record.")
card("Burden", "Long-call tail — share of hours in calls over 4 h", f"""WITH base AS (
  SELECT year, alarm_to_cleared_min AS v
  FROM fact_incident
  WHERE is_actual = 1 AND substr(incident_dt,1,10) >= '2020-01-01'
    AND substr(incident_dt,1,10) >= {DF} AND substr(incident_dt,1,10) <= {DT}
),
cov AS (SELECT year, COUNT(*) AS n, SUM(CASE WHEN v IS NOT NULL THEN 1 ELSE 0 END) AS has
        FROM base GROUP BY year)
SELECT CAST(b.year AS TEXT) || ' (' || SUM(CASE WHEN b.v >= 240 THEN 1 ELSE 0 END)
         || ' calls)' AS year_calls,
  SUM(CASE WHEN b.v >= 240 THEN 1 ELSE 0 END) AS calls_4h_plus,
  ROUND(SUM(CASE WHEN b.v >= 240 THEN b.v END) / 60.0, 1) AS tail_hours,
  ROUND(SUM(b.v) / 60.0, 1) AS all_hours,
  ROUND(100.0 * SUM(CASE WHEN b.v >= 240 THEN b.v ELSE 0 END) / SUM(b.v), 1) AS tail_share_pct
FROM base b JOIN cov c ON c.year = b.year AND c.has * 1.0 / c.n >= 0.9
WHERE b.v IS NOT NULL
GROUP BY b.year ORDER BY b.year""", "bar", 16, 8,
     {"graph.dimensions": ["year_calls"], "graph.metrics": ["tail_share_pct"],
      "graph.y_axis.title_text": "% of incident hours", "graph.show_values": True},
     "Share of all incident hours consumed by calls lasting 4 hours or more, on incident grain "
     "(alarm to last unit cleared). The label under each bar is how many calls that year's "
     "percentage rests on, so 2024's 33% is 11 calls and 2025's 21% is 13 - not hundreds. Hours "
     "inside the tail: 25, 22, 35, 19, 124, 87. Years with under 90% cleared-time coverage are "
     "dropped rather than plotted: 2026 is at 70% because reports are still outstanding, and "
     "plotting it would read as a fall in burden instead of reporting lag.")
card("Burden", "Why incidents are out of scope", f"""SELECT 'mutual aid given' AS reason, COUNT(*) AS incidents FROM {INC} AND COALESCE(mutual_aid_given,'') <> ''
UNION ALL SELECT 'wildland', COUNT(*) FROM {INC} AND incident_class = 'Wildland'
UNION ALL SELECT 'arrival over 25 min', COUNT(*) FROM {INC} AND alarm_to_arrival_min > 25
UNION ALL SELECT 'ALL out-of-scope (distinct)', COUNT(*) FROM {INC} AND NOT ({PERF})
UNION ALL SELECT 'in-scope (performance basket)', COUNT(*) FROM {INC} AND {PERF}""", "table", 24, 5, {},
     "One incident can be excluded for more than one reason, so the reason rows add up to more "
     "than the distinct total.")

SECTIONS = ["Demand", "Response", "Stations", "Map", "Coverage", "Burden"]
METHOD = (
    "**Method.** Window starts 2020-01-01: counts jump 2.19x from 2019 to 2020 (reporting "
    "practice, not workload). Data as of 31 Oct 2026; the last 3 months are under-counted "
    "(reports outstanding) - read the tail as lag, never as falling demand.\n\n"
    "**Two baskets.** Performance KPIs use in-scope incidents only: no mutual aid given, not "
    "wildland, arrival within 25 min. Everything else is reported as burden (unit-hours, "
    "exclusions), because NFPA 1710/1720 treats out-of-scope work as workload, not response "
    "performance.\n\n"
    "**Percentiles are nearest-rank**, matching the analysis scripts - not interpolated. "
    "**Time on scene** comes from the unit grain (arrived to cleared, 4 h cap), never from "
    "incident-level fields. **Station** on an incident is the reporting station, not the unit "
    "that turned out; 42% of incidents carry no station at all and are excluded from by-station "
    "charts. **Staffing** starts Dec 2024, so demand-vs-coverage joins are 2025+ only. "
    "Hover a card and click its info icon for that card's own definition."
)


# Department palette. Red carries the primary series, gold is the accent, navy the third series.
# Navy is used as a lightened tint on charts because pure #073763 disappears on the dark theme.
RED, GOLD, NAVY = "#cc0000", "#bf9000", "#2f6fa8"
SERIES_COLORS = {
    "Monthly demand": {"incidents": RED},
    "Incidents by year": {"incidents": RED},
    "Call-type families": {"incidents": RED},
    "Response time p50/p90 by month": {"p50": RED, "p90": GOLD},
    "Response-time distribution": {"incidents": RED},
    "Percentiles by year": {"p50": RED, "p90": GOLD},
    "Time on scene p50 by month": {"p50": RED},
    "Call volume by station": {"calls": RED},
    "Response time by station (in-scope)": {"p50": RED, "p90": GOLD},
    "Response time by station and staffing state (2025+)": {"p50": RED},
    "Coverage by station (2025+)": {"staffed_pct": RED, "paramedic_pct": GOLD},
    "Paramedic coverage by month": {"Elkhorn (HQ)": "#2f6fa8", "Greenhorn": "#6ba6d6",
                                    "Griffin Butte": "#124a7a"},
    "Work split - performance vs burden": {"Performance": "#2f6fa8", "Burden": GOLD},
    "Long-call tail — share of hours in calls over 4 h": {"tail_share_pct": RED},
}

# A year is a label, not a quantity: stop Metabase thousand-separating it (2,025).
# Belt and braces with the CAST(year AS TEXT) in the SQL above.
for _c in CARDS:
    if _c["name"] in SERIES_COLORS:
        _c["viz"]["series_settings"] = {s: {"color": col}
                                        for s, col in SERIES_COLORS[_c["name"]].items()}
    # pie slice colours live in pie.rows, not series_settings
    if _c["display"] == "pie":
        cols = SERIES_COLORS.get(_c["name"], {})
        _c["viz"]["pie.rows"] = [{"key": s, "name": s, "color": col, "enabled": True}
                                 for s, col in cols.items()]
for _c in CARDS:
    if _c["display"] in ("bar", "line") and "year" in _c["sql"].lower():
        _c["viz"].setdefault("column_settings", {}).setdefault(
            '["name","year"]', {"number_style": "plain", "decimals": 0})


def card_tags(c):
    """Only declare the tags the SQL actually uses."""
    tags = {}
    if DF in c["sql"]:
        tags["start_date"] = DATE_TAGS["start_date"]
    if DT in c["sql"]:
        end = dict(DATE_TAGS["end_date"])
        if c["end_default"]:
            end = dict(end, default=c["end_default"])
        tags["end_date"] = end
    return tags


def api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"X-API-KEY": KEY, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def layout():
    """Flow cards + section headers into the 24-column grid; returns (row, col, sx, sy).

    A wrapped line advances by the tallest card on the line, never by 1, or the next line
    lands on top of the previous one.
    """
    pos = {}
    row = 1
    for section in [""] + SECTIONS:
        cards = [c for c in CARDS if c["section"] == section]
        if section:
            pos[f"__header__{section}"] = (row, 0, 24, 1)
            row += 1
        col, line_h = 0, 0
        for c in cards:
            if col + c["sx"] > 24:
                row += line_h
                col, line_h = 0, 0
            pos[c["name"]] = (row, col, c["sx"], c["sy"])
            col += c["sx"]
            line_h = max(line_h, c["sy"])
        row += line_h
    return pos


def overlaps(pos):
    boxes = [p for k, p in pos.items()]
    bad = []
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            if (a[0] < b[0] + b[3] and b[0] < a[0] + a[3]
                    and a[1] < b[1] + b[2] and b[1] < a[1] + a[2]):
                bad.append((a, b))
    return bad


def main():
    # the Fire Data collection is dedicated to this dashboard: wipe it so rebuilds never
    # leave stale cards from an earlier naming scheme
    _, items = api("GET", f"/api/collection/{COLL}/items")
    for it in items.get("data", []):
        if it.get("model") in ("card", "dashboard"):
            api("DELETE", f"/api/{it['model']}/{it['id']}")

    pos = layout()
    clash = overlaps(pos)
    if clash:
        print(f"  LAYOUT OVERLAP between {len(clash)} pairs, e.g. {clash[:2]}")
    else:
        print("layout: no overlapping cards")
    made = []
    for c in CARDS:
        payload = {"name": c["name"], "display": c["display"], "collection_id": COLL,
                   "dataset_query": {"database": DB, "type": "native",
                                     "native": {"query": c["sql"], "template-tags": card_tags(c)}},
                   "visualization_settings": c["viz"]}
        if c["desc"]:
            payload["description"] = c["desc"]
        st, r = api("POST", "/api/card", payload)
        if not r.get("id"):
            print(f"  FAIL card {c['name']}: {json.dumps(r)[:200]}")
            continue
        made.append((r["id"], c))

    st, dash = api("POST", "/api/dashboard", {
        "name": "SVFD Operations", "collection_id": COLL,
        "description": "Incident demand, response performance, station coverage and burden for Sun Valley "
                       "Fire Department. Combined SVFD and NBCFD unless filtered; out-of-scope work is "
                       "reported as burden rather than mixed into response performance.",
        "parameters": PARAMS})
    did = dash["id"]
    print(f"dashboard id {did}, {len(made)} cards")

    neg, cards = -1, []
    for section in SECTIONS:
        row, col, sx, sy = pos[f"__header__{section}"]
        cards.append({"id": neg, "card_id": None, "row": row, "col": col, "size_x": sx, "size_y": sy,
                      "parameter_mappings": [],
                      "visualization_settings": {
                          "virtual_card": {"name": None, "display": "text", "dataset_query": {},
                                           "visualization_settings": {}},
                          "text": f"## {section}"}})
        neg -= 1
    for cid, c in made:
        row, col, sx, sy = pos[c["name"]]
        tags = card_tags(c)
        pm = []
        if "start_date" in tags:
            pm.append({"parameter_id": PARAM_FROM["id"], "card_id": cid,
                       "target": ["variable", ["template-tag", "start_date"]]})
        if "end_date" in tags:
            pm.append({"parameter_id": PARAM_TO["id"], "card_id": cid,
                       "target": ["variable", ["template-tag", "end_date"]]})
        cards.append({"id": neg, "card_id": cid, "row": row, "col": col, "size_x": sx, "size_y": sy,
                      "parameter_mappings": pm, "visualization_settings": {}})
        neg -= 1
    bottom = max(p[0] + p[3] for k, p in pos.items() if not k.startswith("__")) + 2
    cards.append({"id": neg, "card_id": None, "row": bottom, "col": 0, "size_x": 24, "size_y": 6,
                  "parameter_mappings": [],
                  "visualization_settings": {
                      "virtual_card": {"name": None, "display": "text", "dataset_query": {},
                                       "visualization_settings": {}},
                      "text": METHOD}})
    st, out = api("PUT", f"/api/dashboard/{did}", {"dashcards": cards})
    print("layout:", st, "dashcards:", len(out.get("dashcards", [])) if isinstance(out, dict) else out)

    # Every rebuild creates a new dashboard, which orphans the previous public UUID - the apex would
    # point at a 404. Re-enable the public link and record the state the redirector reads.
    st, link = api("POST", f"/api/dashboard/{did}/public_link")
    pub = link.get("uuid")
    with open(os.environ.get("MB_STATE_FILE", os.path.expanduser("~/metabase/state.json")), "w") as fh:
        json.dump({"dashboard_id": did, "public_uuid": pub}, fh, indent=2)
    print("public uuid:", pub, "| state.json written")

    bad = 0
    for cid, c in made:
        st, res = api("POST", f"/api/card/{cid}/query")
        if res.get("status") != "completed":
            bad += 1
            print(f"  QUERY FAIL {c['name']}: {json.dumps(res.get('error'))[:300]}")
    print(f"queries: {len(made)-bad}/{len(made)} completed")
    print("KPI layout:", {c["name"]: pos[c["name"]] for c in CARDS if not c["section"]})
    print(f"URL: {BASE}/dashboard/{did}")


main()
