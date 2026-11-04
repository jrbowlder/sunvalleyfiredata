# Building the Tableau Public viz

Four sheets, fed by four CSVs generated from the **synthetic** database — no real records.

```bash
python3 etl/export_tableau.py                     # writes data/tableau/*.csv
python3 etl/export_tableau.py --db warehouse.db   # the real build, if ever needed internally
```

| File | Rows | Use |
|---|---|---|
| `incidents.csv` | 3,789 | demand trend, call-type mix |
| `response_monthly.csv` | 82 | response percentiles over time |
| `coverage_monthly.csv` | 44 | staffed days and paramedic days |
| `locations.csv` | 3,405 | map sheet (optional) |

Every definition matches the Streamlit app: two baskets (`basket` = `in scope` / `burden`),
nearest-rank percentiles, and the section-coverage rule. Percentiles are pre-computed on
purpose — Tableau's `PERCENTILE` is a table calculation whose answer changes with the
view's level of detail, so a reader who adjusts a filter would get a different number than
the count beside it.

## Sheet 1 — Demand over time

Data: `incidents.csv`.
- `month_start` to Columns (a real date, so Tableau gives you the year/quarter/month
  hierarchy), `incident_uid` (Count) to Rows.
- `family` to Colour for a stacked view.
- Filter `incident_date` to 2020-01-01 onwards, and add a reference band or annotation over the last
  three months labelled "reports still outstanding".
- Expected: 429 incidents in 2020 rising to 675 in 2025, 554 in the ten months to October
  2026.

## Sheet 2 — What kind of calls

Data: `incidents.csv`.
- `family` to Rows, Count to Columns, sorted descending. Expect EMS 1,889, Fire 808,
  Service 683, MVA 228, Wildland 99, Backcountry rescue 53, and a small **Untyped** bar of
  29.
- Add a second sheet with `subtype` nested under `family` if you want the drill-down the
  app shows as a treemap.

## Sheet 3 — Response performance

Data: `response_monthly.csv` (in-scope only).
- `month_start` to Columns, `response_p50` and `response_p90` to Rows as two lines.
- Expected: p50 hovering around 7 minutes, p90 around 13, with no upward drift across the
  series — that flatness is the finding worth annotating.
- Put `timed_incidents` on the tooltip so the sample size travels with the number.

## Sheet 4 — Coverage

Data: `coverage_monthly.csv`.
- `month_start` to Columns, `station` to Colour.
- Two measures: `staffed_days / station_days` and `medic_days / station_days`, as a
  stacked bar or two lines. Elkhorn runs near every day; Greenhorn moves from about half
  to most days across 2025-26.

## Dashboard assembly

1. Stack sheets in reading order: demand, call types, performance, coverage.
2. Put the data cut-off and the word **synthetic** in a text box — a reviewer must never
   have to guess whether the numbers are real.
3. Publish, then copy the viz URL into `README.md` under the screenshot.

## Numbers to check yourself against the app

| Metric | Value |
|---|---|
| Incidents 2020 to date | 3,789 |
| In-scope share | 90% (3,398 of 3,789) |
| Response p50 / p90 | 7.2 / 13.3 min |
| Untyped | 29 of 3,789 |
| Geocoded | 90% |
