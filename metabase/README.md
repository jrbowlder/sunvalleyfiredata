# Metabase layer

The warehouse in this repo is the source of truth; this directory is the code that turns it into
the dashboard the department actually looks at.

Live: <https://sunvalleyfiredepartment.com> (public, no login); admin view at
`dash.sunvalleyfiredepartment.com`.

## What this is

`build_dashboard.py` builds the whole dashboard through Metabase's HTTP API rather than by clicking:
25 question cards across Demand, Response, Stations, Map, Coverage and Burden, plus the From/To date
filters, laid out on a grid and reconciled against the analysis scripts.

It is idempotent: it wipes the target collection and rebuilds, then runs every card's query and
reports which ones failed, so a rebuild either reproduces the dashboard or tells you what broke.

```bash
export MB_URL=http://127.0.0.1:3030
export MB_API_KEY_FILE=~/metabase/api-key.txt   # an admin API key from Metabase
python3 build_dashboard.py
```

## Decisions worth knowing

- **Two baskets.** Response KPIs use in-scope incidents only (no mutual aid given, not wildland,
  arrival within 25 min). Excluded work is reported as burden, never folded into performance.
- **Nearest-rank percentiles.** SQLite has `percentile()`, but it interpolates. The cards use a
  `ROW_NUMBER() … MIN(n-1, CAST(n*q AS INT))+1` window so p50/p90 match the printed analysis to the
  decimal instead of drifting.
- **Time on scene is incident grain** (`alarm → last unit cleared` minus `alarm → first arrival`).
  The per-apparatus measure stops with the NFIRS era, so only the incident measure spans every year;
  where both exist they agree within 0.8–3.0 minutes.
- **Thin data is dropped, not plotted.** Months with fewer than 10 derivable incidents are omitted,
  and the long-call tail drops years under 90% cleared-time coverage, so a reporting lag never reads
  as a fall in demand.
- **Palette.** Red `#cc0000` for primary series, gold `#bf9000` for the p90 tail and the burden
  slice, navy `#2f6fa8` where a third series is needed. Metabase OSS will not let a pin map colour
  points by category, so the map is location only.
- **Paramedic coverage is an Elkhorn commitment.** The monthly chart filters to `station_code =
  'ELK'` and the station chart returns `NULL` medic coverage for every other station: a
  medic-certified member assigned to Greenhorn is a credential, not a service the department runs.
  Both cards once split by station, and Metabase summed the split percentages (100% + 35%), which is
  how a coverage chart came to show 155%.
- **The public link self-heals.** Each rebuild creates a new dashboard, so the builder re-enables its
  public link and writes `state.json` with the dashboard id and public UUID; the redirector reads
  that per request instead of a hardcoded UUID.
- **Refresh.** `python3 etl/build.py` then `~/metabase/after-etl.sh`: new rows appear immediately
  because native SQL reads the file per request, but the dashboard's To-date default is baked into
  the dashboard and has to be moved forward, or the public view quietly keeps ending at the old
  month.
