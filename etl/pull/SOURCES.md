# Sources and refresh procedure

Everything the warehouse is built from, and how to refresh it.

## Sources

| Source | What | Pulled by |
|---|---|---|
| Elite `CAPSTONE Incident Fact` (CSV) | NFIRS-era incidents | report export |
| Elite `CAPSTONE Apparatus Fact` (CSV) | unit responses | report export |
| Elite NERIS incident fact (CSV) | NERIS-era incidents - **no address column** | report export |
| Elite NERIS Fire incident list (browser) | `NerisFullAddress` + `Primary Incident Type` per incident | `elite_neris_incidents.py` |
| First Due reports (JSON) | schedule, certifications, groups | `fd_pull.py` |
| OSM Overpass extract | street and place reference for geocoding | `osm_streets.py` |

The incident-list pull exists because the NERIS-era CSV export carries neither an address
nor a usable incident type. Elite's NERIS Fire incident list carries both
(`NerisFullAddress`, `Primary Incident Type`), and its default filter is only the last 60
days - the script widens the date range before paging. 715 incidents: 711 with an address,
630 with a type.

## How new data gets in

There are two halves and only one of them is automatic:

```
  PULL  (manual, needs a logged-in browser)      BUILD  (scripted, repeatable)
  ----------------------------------------       -----------------------------
  Elite CSV exports          ─┐
  First Due JSON pulls       ─┴─> data/raw/  ──>  python3 etl/build.py  ──> warehouse.db
  curated medic list       ──> data/curated/
```

`etl/build.py` deletes `warehouse.db` and rebuilds it from `data/raw/` in three stages
(NFIRS -> NERIS -> staffing). It is deterministic: the same raw files always produce the
same warehouse. The pull side is **not** scriptable end to end, because both systems need
an interactive login and the session lives in a real browser window.

### Refresh steps

1. **Open the browser window with a debug port** (once per session):

   ```bash
   google-chrome-stable --ozone-platform=x11 --remote-debugging-port=9222 \
     --user-data-dir="$HOME/.cache/elite-chrome" --no-first-run about:blank
   ```

   Log in to Elite in one tab and First Due in another by hand. Credentials and 2FA are
   never handled by the assistant.

2. **Pull the Elite reports** (three CSV exports). `etl/pull/fetchfile.py` downloads a URL
   using the logged-in session and writes it to `data/raw/`:

   ```bash
   python3 etl/pull/fetchfile.py "<export url>" data/raw/elite_incident_fact.csv
   python3 etl/pull/fetchfile.py "<export url>" data/raw/elite_apparatus_fact.csv
   python3 etl/pull/fetchfile.py "<export url>" data/raw/elite_neris_incident_fact.csv
   ```

   The export URL is
   `.../rwelite/app/controller.cfm?method=cnt_adhoc.export&reportID=<REPORT>&reportVersionID=<VERSION>&type=csv`

3. **Pull the First Due reports** (JSON, paged automatically by `etl/pull/fd_pull.py`):

   ```bash
   python3 etl/pull/fd_pull.py 104884 data/raw/fd_shift_coverage.json
   python3 etl/pull/fd_pull.py 71261  data/raw/fd_certifications.json
   python3 etl/pull/fd_pull.py 103415 data/raw/fd_personnel_shift.json
   ```

   `fd_groups.json` comes from the API directly (see table below).

4. **Rebuild:** `python3 etl/build.py`

## The sources

### ImageTrend Elite (`idaho.imagetrendelite.com`)

| Source | Report ID | Dataset | Fields | Output |
|---|---|---|---|---|
| `CAPSTONE Incident Fact` | `7D6BA959-9928-4EA4-AE57-FD291FF853E6` | Fire Incidents (NFIRS) | 29 | `elite_incident_fact.csv` |
| `CAPSTONE Apparatus Fact` | `70C138C9-2EA4-4200-8C59-FAD643CE5D8A` | Fire Incidents (NFIRS) | 17 | `elite_apparatus_fact.csv` |
| *unnamed NERIS report* | `0549444C-5655-4130-9D1D-6939A95D0443` | NERIS Fire | 18 | `elite_neris_incident_fact.csv` |

`reportVersionID` is **volatile**: it changes every time a report's columns are edited.
Store the `reportID` (stable) and resolve the version at pull time by opening the report
and reading the hidden `reportVersionID` field. Do not hard-code versions.
Current versions at the time of writing: incident `68378EF9-…`, apparatus `6FA4F23F-…`,
NERIS `2078A632-…`.

The NERIS report is still unnamed in Elite, worth renaming to `CAPSTONE NERIS Incident
Fact` for consistency and for anyone else browsing the report library.

### First Due (`sizeup.firstduesizeup.com`)

| Source | ID | Endpoint | Fields | Output |
|---|---|---|---|---|
| `SHIFT COVERAGE - TEST` | `104884` | `POST /report/getResultList` | date, person, qualifier, rank, work type, assignment | `fd_shift_coverage.json` |
| `SVFD-PERS: Active Certifications by Member` | `71261` | `POST /report/getResultList` | person, cert, issue, expiry | `fd_certifications.json` |
| `Personnel Shift Distribution` | `103415` | `POST /report/getResultList` | shift, headcount, pct | `fd_personnel_shift.json` |
| Groups (EMT / Paramedic / POC / …) | n/a | `GET /shift/group/getGroups` | group, members | `fd_groups.json` |

Two details that cost time to discover: requests need
`X-Requested-With: XMLHttpRequest` or the server returns the SPA shell instead of JSON,
and the session cookie is HttpOnly so a cookie jar copied out of the browser returns
`200` with a zero-byte body. Always fetch from inside the page.

### Curated (not pulled)

| File | What | Maintained by |
|---|---|---|
| `data/curated/dim_person_medic.csv` | 11 medics with `medic_since` and a provenance basis | hand-checked with Jimmy |

This exists because neither First Due source is sufficient: group membership has no
history, and 4 of 9 medics have no paramedic credential on file.

### Deliberately excluded

- **First Due incident/unit-hour reports**: the module is unpopulated (7 rows for 2026,
  0 for 2025 and 2021–2024).
- **First Due time clock**: not used; payroll is elsewhere.
- **EPCR / patient data**: out of scope by policy.
- **Elite Hydrants / Inventory datasets**: inventory lives in the separate
  `~/Documents/SVFD_Inventory` project; hydrants are not in the capstone.

## Known fragility

- Report columns are edited in the Elite UI, not in code. If someone changes a column
  list, the CSV shape changes and the loader's `col()` matcher may silently pick a
  different field. Re-check the column count after any report edit.
- `SHIFT COVERAGE - TEST` is a custom report with no date filter, so its window moves as
  First Due's default range moves. Confirm the date range after each pull.
- Published future schedule is included in the staffing pull; `is_actual` marks it.
