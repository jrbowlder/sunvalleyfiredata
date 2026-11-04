# SVFD Capstone — schema of the synthetic (public) build

This document describes the **synthetic build** that ships with the public copy of this
repository. It is the same schema, the same grain and the same metric definitions as the
department's real warehouse; every person, street address, incident number and timestamp
is generated.

Nothing here descends from a real record. The only shared artefact is the public OSM
street reference the geocoder matches against, which is already public data.

Regenerate the whole build from scratch:

```bash
python3 etl/synthetic.py                                    # deterministic, seed 20260101
PYTHONHASHSEED=0 \
SVFD_RAW=data/synthetic/raw \
SVFD_CURATED=data/synthetic/curated \
SVFD_CLEAN=data/synthetic/clean \
SVFD_DB=data/synthetic/warehouse.db \
SVFD_OSM=data/raw \
python3 etl/build.py
```

`PYTHONHASHSEED=0` is not decoration. The loaders iterate a few sets of strings, and
Python randomises its hash seed per process, so insertion order — and therefore the
database file's bytes — changes between runs even though every table's content is
identical. With the seed pinned, the build is byte-for-byte reproducible.

The `SVFD_*` environment variables are the only difference between this build and the
private one. Unset, every script reads and writes where it always did.

## At a glance

| Table | Grain | Rows |
|---|---|---|
| `fact_incident` | one row per incident | 3,802 |
| `fact_unit_response` | one row per apparatus per incident | 5,948 |
| `fact_staffing_day` | one row per person, day and assignment | 7,189 |
| `dim_person` | one row per person | 31 |
| `dim_person_medic` | one row per person with a paramedic claim | 6 |
| `dim_apparatus` | one row per rig, canonical code | 10 |
| `dim_station` | one row per station | 3 |
| `dim_incident_type` | one row per NFIRS code seen | 32 |
| `dim_certification` | one row per credential | 121 |
| `dim_person_group` | one row per person-group membership | 109 |
| `dim_geo_street` | one row per street in the local reference | 3,781 |
| `dim_shift` | shift rotation | 3 |

Thirteen incident rows carry no date at all (the untyped tail — see "Deliberate
deviations"); they are counted in the total but have a null `year`.

## Grain

Three facts, three grains. Mixing them is the fastest way to a wrong number.

| Table | Grain | Key |
|---|---|---|
| `fact_incident` | one row per incident | `incident_uid` (surrogate) |
| `fact_unit_response` | one row per apparatus per incident | `unit_row_id` |
| `fact_staffing_day` | one row per person, day and assignment | `staffing_id` |

Derived measures — response time, time on scene, unit-hours — are computed at query time
from stored timestamps and are never stored as typed values. A definition can change
without a rebuild, and any figure can be walked back to the field it came from.

### Why the incident key is a surrogate

Incident numbers repeat **across years**, and the department mixes three numbering
schemes (`24-454`, `2000123`, `SVFD202600123`). A business key is not a primary key until
it is proven unique across history, so `fact_incident` carries `incident_uid` and the
human-readable number lives in `incident_key`. In the synthetic build the same collisions
are present on purpose, because they are the reason the surrogate exists.

## Dimensions

### Stations

| Code | Station | Notes |
|---|---|---|
| `ELK` | Elkhorn | headquarters, staffed daily |
| `GH` | Greenhorn | staffed part of the period |
| `GB` | Griffin Butte | unstaffed; reporting location only |

Source files spell these several ways (`Station 2` and `Station 3` are obsolete naming the
department dropped). The loaders normalise every spelling to the canonical code and keep
the original in `station_raw`.

### Apparatus

Canonical codes with the spellings crews actually use:

| Code | Raw spellings in the source |
|---|---|
| `E60` | `E60`, `ENG60`, `Engine 60`, `E 60` |
| `E61` | `E61`, `ENG61`, `Engine 61` |
| `E62` | `E62`, `ENG62`, `Engine 62` |
| `E64` | `E64`, `ENG64`, `Engine 64` |
| `T61` | `T61`, `TANK61`, `Tanker 61` |
| `T62` | `T62`, `Tanker 62` |
| `M60` | `M60`, `Medic 60` |
| `M62` | `M62`, `Medic 62` |
| `SQD1` | `SQ1`, `SQD1` |
| `C1` | `C1`, `Chief 1` |

### Personnel (every name invented)

Thirty-one people. Names, ranks, rotation, certifications and paramedic status are all
generated; the roster is written out explicitly rather than randomised so that crew size,
rotation and coverage are reproducible and easy to reason about.

Barrett Quill · Bram Okafor · Camille Bostock · Colin Amberly · Corinne Ashby ·
Dana Kestrel · Delia Vankirk · Desmond Marchetti · Eli Thornquist · Fenna Groat ·
Grant Mullane · Hattie Vance · Ida Wexley · Ivan Prosser · Kira Wintle · Maejor Falk ·
Marcus Delacroix · Milo Fenno · Nadia Farrow · Nona Pfaff · Otto Blasingame ·
Perry Lattimore · Priya Sandoval · Ruben Otanez · Sasha Renard · Soren Bellamy ·
Tess Halloway · Tobias Rennick · Wendell Bryer · Wren Sable · Yolanda Frith

### Paramedic status: a curated dimension with provenance

Neither source system can answer "was a paramedic on duty" on its own, so
`dim_person_medic` is curated and every row states its basis:

| Person | `medic_since` | `basis` |
|---|---|---|
| Colin Amberly | 2021-03-15 | `certification` |
| Corinne Ashby | 2022-02-01 | `certification` |
| Desmond Marchetti | 2019-06-01 | `certification` |
| Wren Sable | 2025-10-01 | `certification` |
| Nona Pfaff | 2026-05-01 | `roster_confirmed` |
| Kira Wintle | 1900-01-01 | `estimated` |

- `certification` — the date comes from a paramedic credential's issue date; auditable.
- `roster_confirmed` — a date supplied by the department, no certificate on file.
- `estimated` — no date available. The sentinel `1900-01-01` is deliberately **excluded**
  from coverage calculations (`v_station_day` joins only on `certification` and
  `roster_confirmed`), so an unknown date never silently becomes "a medic since 1900".

### Certifications

`dim_certification` is temporal: one row per credential with `issue_date` and
`expiration_date`. Lapsed credentials are a **data-quality flag with a review list**, not
a coverage gate — gating on expiry would move a station's paramedic coverage by tens of
percentage points on the strength of one unrenewed record.

### Incident type

`dim_incident_type` maps an NFIRS code to a two-level taxonomy. Classification is driven
by the **code**, with the free-text description used only as a fallback. Thirty-two codes
appear in this build.

## Call-type taxonomy

| Family | Subtypes |
|---|---|
| Fire | Structure fire · Vehicle fire · Alarms · HazMat · Other fire |
| Wildland | Forest / timber · Brush / grass · Vegetation, other |
| EMS | EMS call · Medical assist · Rescue / EMS, other |
| Motor vehicle accident | With injury · No injury · Vehicle extrication · Cleanup / other |
| Backcountry rescue | Search · Water / ice · Other rescue |
| Service | Event coverage / standby · Public assist · Cancelled / no incident found · Weather & disaster · Other service |
| Untyped | No type code on the report |

A vehicle extrication is an MVA with entrapment, so it belongs with the accidents, not
with backcountry rescue. **Mutual aid is a flag** (`fact_incident.mutual_aid_given`), never
a type — a mutual-aid structure fire is still a structure fire.

`etl/taxonomy.py` is the single definition. An unknown code falls back to its NFIRS
series, so a new code cannot silently become `Untyped` and vanish from a chart.

## Deliberate deviations from the real pull

The generator reproduces the real data's shape and its defects, with three exceptions.
Each is a choice about what a public demo needs, and each is visible in the numbers.

| Behaviour | Real pull | Synthetic build | Why |
|---|---|---|---|
| NERIS-era type codes | `Dispatch Incident Code` blank on 98% of rows; `Incident Type` is the constant `Fire NERIS` | 97% of rows carry the incident's call-type code | Call type is the backbone of the dashboard; an all-`Untyped` 2026 would show an empty treemap |
| NERIS-era unit coverage | ~5% of incidents have apparatus rows | **55%** | Unit-level charts are the point of the portfolio; 5% leaves nothing to plot |
| Personnel | Real names, emails, certifications | 31 invented people | Privacy |

Everything else mirrors the real source: three incident-number schemes, repeated numbers
across years, two timestamp families in the NERIS era, sparse lifecycle fields, mixed
station and apparatus spellings, and a reporting-lag month shape in the newest year.

The 3% of NERIS rows left untyped carry no code and no date. They land in the `Untyped`
bucket with a null `year`, which keeps the untyped path exercised instead of deleting it.

## Metric definitions

| Metric | Definition | Grain |
|---|---|---|
| `demand` | count of in-scope incidents | incident |
| `response_time` | alarm → first unit arrival | incident |
| `turnout_time` | dispatch → en route | unit |
| `travel_time` | en route → arrival | unit |
| `time_on_scene` | arrived → cleared | unit |
| `unit_hours` | cleared − dispatch, summed | unit |
| `coverage_days` | days with at least one medic-qualified person on a station assignment | staffing |

Percentiles use **nearest-rank** (`sorted(v)[int(n*q)]`) everywhere. Pandas `quantile()`
interpolates and disagreed by up to 0.2 min on the same year, so the analysis scripts and
the dashboard share one implementation.

### Two baskets, not one filter

**Performance** uses in-scope incidents only: no mutual aid given, not a wildland
deployment, arrival within 25 minutes. Everything excluded is reported separately as
**burden** — unit-hours consumed — following the NFPA 1710/1720 convention that
out-of-scope work is workload, not response performance. A response percentile is never
quoted without the share of work it covers.

## Limits of this build

- **It is synthetic.** The distributions are plausible, not measured. No operational
  conclusion in this repository applies to any real department.
- **The newest months are deliberately incomplete.** The generator reproduces the
  reporting-lag shape, so the tail of 2026 must never be read as a decline.
- **Staffing starts 2024-12-31**, so coverage questions cannot extend backwards.
- **Location is street-level, not address-level.** About 90% of incidents are placed at a
  street centroid; the rest are unmatched by design (invented street names that the OSM
  reference does not carry) or have no address.
- **Jurisdiction is only resolvable from 2026.** The NFIRS era carries no reliable
  SVFD/NBCFD signal, so jurisdiction comparisons start there and say so.
