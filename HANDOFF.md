# Handoff

State of the capstone project for a fresh session. Everything below is on disk; nothing
lives only in a conversation.

## Where things are

```
~/Documents/SVFD_Capstone/
  warehouse.db            built artefact - 5,548 incidents, 6,872 unit rows, 5,021 staffing rows
  data/raw/               immutable source pulls (3 Elite CSVs, 4 First Due JSON)
  data/curated/           dim_person_medic.csv - hand-checked medic list, 11 people
  data/clean/             generated review files (identity conflicts, unresolved org)
  etl/                    load_elite.py, load_neris.py, load_staffing.py, build.py
  etl/taxonomy.py         call-type taxonomy - NFIRS code -> (family, subtype)
  etl/load_geo.py         offline geocoding of incident addresses -> fact_incident.geo_*
  etl/pull/osm_streets.py one-time Overpass pull of the OSM street/place reference
  etl/pull/               cdp.py, fetchfile.py, fd_pull.py, net_capture.py + SOURCES.md
  analysis/               01_demand_performance.py, 02_demand_vs_coverage.py
  docs/                   SCHEMA.md (decisions + defects), FINDINGS.md (six findings)
```

## What works

- `python3 etl/build.py` rebuilds `warehouse.db` from `data/raw/` in three stages.
  Deterministic, verified with integrity + referential checks, zero duplicate keys.
- `python3 analysis/01_demand_performance.py` - demand, response percentiles, time on
  scene, unit-hours.
- `python3 analysis/02_demand_vs_coverage.py` - demand against the staffing calendar.
- `.venv/bin/streamlit run app/app.py` - the dashboard (first cut, verified running).
  Deps pinned in `requirements.txt`, installed into `.venv/` with `uv pip install`.
  Five tabs: Demand (monthly + annual + call-type treemap, incomplete tail shaded), Response
  (monthly p50/p90, distribution vs the 25-min cap, yearly percentiles, time on scene),
  Stations (volume, p50/p90 by station, staffed vs unstaffed), Coverage (staffed and
  paramedic days, paramedic trend, calls/day by crew size), Burden (in/out-of-scope
  split, unit-hours, exclusion reasons) plus a metric-definitions expander.
  Sidebar filters: date range, station, jurisdiction, call type, station-unknown toggle.
  Call types: a family bar plus a full-width treemap that zooms family -> subtype, driven
  by `etl/taxonomy.py` (NFIRS code first, description only as fallback).
  Every figure is reproducible from the scripts above; the dashboard applies the same
  two-basket and cut-off rules and quotes the data-as-of date on the page.
- `etl/pull/SOURCES.md` documents every source, its report ID, and the refresh steps.

## Headline numbers (as of 2026-10-31)

- Demand: 429 incidents (2020) -> 675 (2025), +57%. 2026 running +25% YoY per day.
- Response p90: 13.0-14.7 min every year of the series - no degradation.
- Time on scene p50: 14-19 min, stable.
- Staffing is unrelated to demand (2 vs 3 crew -> 2.09 vs 2.10 calls/day).
- Greenhorn: staffed 46% of days (2025) -> 93% (2026).

## Repo docs

- `README.md` (root) - portfolio entry point: problem, mermaid architecture diagram,
  quickstart, data model, metric definitions, headline findings, decisions, limitations,
  privacy note, roadmap. Screenshot at `docs/img/dashboard.png` (real aggregates - swap
  for a synthetic-data shot before the repo goes public).
- `.gitignore` excludes `.venv/`, `data/raw/`, `data/clean/` and `warehouse.db`, so raw
  department material cannot be committed by accident.
- `requirements.txt` pins streamlit/pandas/plotly for the app only; the ETL and analysis
  scripts are standard library only.

## Geography (added Nov 2026)

- `data/raw/osm_streets.csv` (name + route `ref`) and `osm_places.csv` (named buildings)
  are the geocoding reference, pulled once by `etl/pull/osm_streets.py`. Both are public
  OSM data and are deliberately un-ignored so the repo geocodes without Overpass.
- `etl/load_geo.py` matches `fact_incident.street_name` against that reference and writes
  `geo_lat` / `geo_lon` / `geo_source`. Precision is per row: `reported_point`,
  `street_centroid`, `street_centroid_broad` (>2 km spread). Unmatched streets go to
  `data/clean/geo_unmatched.csv`.
- Final coverage: **4,625 of 5,513 actual incidents placed (84%)** - 248 reported points,
  3,098 street centroids, 1,255 broad-street centroids, 912 unplaced. By year: 2013-2025
  runs 69-91%, 2026 79%.
- The 2026 hole was closed by `etl/pull/elite_neris_addresses.py`: the NERIS-era CSV has
  no address column, but Elite's NERIS Fire incident list exposes `NerisFullAddress`, so
  715 incidents (711 with an address) were scraped once into
  `data/raw/elite_neris_addresses.csv`. `load_geo.py` backfills street/city from it before
  matching, and cleans up address noise ("City of" suffix, "MM 143 STATE HIGHWAY 75",
  "A Road / B Road" intersections). 2026 went from 2% to 79% placed.
- NERIS incident **types** are recovered the same way: `etl/pull/elite_neris_incidents.py`
  scrapes `Primary Incident Type` from the same Elite grid, and
  `etl/taxonomy.py::classify_neris()` maps the NERIS vocabulary onto the families.
  Untyped fell from 682 to **83 (1.5%)**, all records where no type was ever entered.
- Still unplaceable: 912 incidents. 721 of those have no address anywhere, and the
  remainder are private developments OSM does not map (NEW VILLAGER CONDO, MORNINGSTAR,
  HORSEMAN CENTER). A curated `data/curated/geo_aliases.csv` would fix those; judged not
  worth hand-matching ~470 names.
- What the second pass fixed: route numbers now come from OSM's `ref` tag (the road crews
  call Highway 75 is named "Idaho Medal of Honor Highway" in OSM), a bare "75" in the
  street field is treated as the route, and place-style suffixes ("... CONDO",
  "... CENTER") are stripped so building names can match.
- Ceiling: the remaining top unmatched are private developments OSM does not map at all -
  NEW VILLAGER CONDO (23), MORNINGSTAR (19), HORSEMAN CENTER (17), SUMMIT 1 CONDO (11).
  Resolving them needs a curated `data/curated/geo_aliases.csv` (name -> point or street)
  supplied by someone who knows the valley, exactly like the hand-checked medic list.
- The NERIS-era export carries no address, so 2025-26 cannot be mapped at all. Say so
  wherever the map is shown.

## Synthetic build (public copy)

- `etl/synthetic.py` — fixed seed (20260101), stdlib only, writes `data/synthetic/raw/`
  (7 inputs, real header dialects) and `data/synthetic/curated/dim_person_medic.csv`.
  Deterministic: a rebuild reproduces the committed database exactly.
- Env overrides are the only fork in the road: `SVFD_RAW`, `SVFD_CURATED`, `SVFD_CLEAN`,
  `SVFD_DB`, and `SVFD_OSM` for the shared public street reference. Unset, everything
  behaves as before; the real `warehouse.db` and `data/raw/` were byte-identical after
  the synthetic work.
- Verified numbers: 3,802 incidents / 5,948 unit responses / 7,189 staffing rows / 31
  people; p50 7.2, p90 13.3; basket 89.2%; 90% of incidents geocoded; paramedic on 79% of
  actual staffed station-days; zero real names, zero reused coordinates.
- **Decided (Nov 2026):** the public build **types the NERIS era** — 97% of rows carry the
  incident's call-type code, so 2026 reads EMS 275 / Fire 120 / Service 101 / MVA 36 /
  Wildland 17 / Backcountry rescue 5 instead of 100% `Untyped`. And **NERIS unit coverage
  is 55%** (`NERIS_UNIT_SHARE = 0.625`, which lands at 55.8% after the 0.6x discount on
  dispatch-numbered rows). The 3% left untyped carry no date either and land in `Untyped`
  with a null year, which keeps the untyped path exercised.
- Docs for the shipped build: `docs/SCHEMA_SYNTHETIC.md` (schema, invented roster,
  generator parameters, deviations) and `docs/FINDINGS_SYNTHETIC.md` (the results it
  produces). `docs/SCHEMA.md` and `docs/FINDINGS.md` stay the private build's write-ups.
- Still open before publishing: **`docs/SCHEMA.md` and this file still contain real
  personnel names** — neither ships; regenerate `docs/img/*.png` from the synthetic build
  (the current screenshots are real aggregates); refresh the README headline numbers, which
  still quote the private dataset.

## What's next

1. ~~**App** - Streamlit dashboard~~ **done as a first cut** (see above). Still open:
   a 2-minute demo path for the Chief, and the one Tableau Public viz.
2. **Synthetic data + packaging** - README is written. Still needed: the synthetic data
   generator (same schema, plausible distributions) so the repo runs publicly with zero
   real records, the 2-minute demo path for the Chief, and `git init` (this directory is
   not a repo yet).
3. **Validate** - reproduce a few of ImageTrend's own canned reports (Fractile Response
   Times, Average Time on Scene) from the warehouse to prove correctness. The dashboard
   already agrees with `analysis/01` figure for figure, which is the internal half of
   this check; the vendor half still needs a screenshot of their report.

## Open questions for Jimmy

- Medic start dates for Hannah Baybutt, Nadav Cohen, Zack Traylor (carried as `estimated`).
- Whether to pursue the incident-number standardisation on dispatch numbers - would make
  jurisdiction resolvable permanently.
- Whether historical schedules exist before Dec 2024.

## Gotchas that will bite

1. **`Incident Date Time (itfiIncident.004)` is date-only.** Using it as an alarm time
   fabricates multi-hour responses (it parses to midnight). It may bucket incidents, never
   compute an interval. This already caused one bug.
2. **Incident numbers repeat across years.** Never join on the number alone; pair with year.
   `fact_incident` uses a surrogate `incident_uid`.
3. **The NERIS export has no incident type.** `Incident Type` is the constant string
   "Fire NERIS" on all 737 rows and the dispatch code is blank on 719, so the NERIS era
   cannot be typed from that pull. They are typed instead from Elite's NERIS incident
   list (`Primary Incident Type` -> `etl/taxonomy.py::classify_neris`), which leaves
   **83 Untyped (1.5%)** — records with no type entered at all.
4. **Elite `reportVersionID` is volatile** - store `reportID`, resolve version at pull time.
5. **First Due needs `X-Requested-With: XMLHttpRequest`** or the server returns the SPA
   shell instead of JSON. Session cookie is HttpOnly, so fetch from inside the page.
6. **Unit-level data stops at the NFIRS era** - only 32 of 693 incidents in 2026 have
   apparatus rows. Never publish a unit-level number for 2026 without saying so.
   Consequence: unit-hours and time-on-scene charts must omit a year whose unit coverage
   is thin (2026: ~2% of incidents have apparatus rows) rather than plot it as zero -
   a 0.05 h/day bar reads as a workload collapse, not as missing data.
7. **The repeated incident number also breaks unit joins.** `fact_unit_response` carries
   no surrogate, so joining units to incidents on `incident_key` alone duplicates the 7
   unit rows that belong to duplicated numbers. Join with a `ROW_NUMBER() OVER (PARTITION
   BY unit_row_id ...)` that prefers the incident whose year matches the unit's own
   `dispatch_dt`; that yields exactly 6,835 matched rows (6,872 minus the 37 with a null
   incident number). The `IN (SELECT incident_key ... WHERE year = ?)` pattern in the
   analysis scripts has the milder version of the same ambiguity.
8. **Percentiles must use one convention.** The analysis scripts use nearest-rank
   (`sorted(v)[int(n*q)]`); pandas `quantile()` interpolates and disagreed by up to
   0.2 min on the same year. The app now imports the scripts' convention, so the
   dashboard and the printed report cannot drift apart.
9. **The newest months are incomplete** (reporting lag). Never read the trailing 3 months
   as a trend without a stated cut-off.
10. **Browser driving**: cua-driver can't see native-Wayland windows on Hyprland; launch
   Chrome with `--ozone-platform=x11 --remote-debugging-port=9222`.
