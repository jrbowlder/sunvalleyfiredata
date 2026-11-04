# SVFD Capstone data schema

Working draft. Grain, keys, sources and known defects for the warehouse behind the
capstone project. Nothing here is final until the source quirks are locked down.

## The thesis

The department cannot answer basic operational questions (call volume by station at
an arbitrary time scale, medic coverage days, whether response performance changed as
demand grew) because **every identifier in the source systems has two to five
spellings, and the two systems each hold only half the answer.**

- Elite knows what happened (incidents, unit times) and not who was available.
- First Due knows who was available (schedule, qualifiers) and not what happened.
- Neither can answer the pair.

The warehouse resolves identity first, then measures.

## Sources

| Source | What | Coverage | Pull method |
|---|---|---|---|
| Elite `CAPSTONE Incident Fact` | one row per incident | 2011-01 → 2026+ (4,942 rows) | CSV export by reportID |
| Elite `CAPSTONE Apparatus Fact` | one row per unit response | same span (6,872 rows) | CSV export by reportID |
| First Due `SHIFT COVERAGE - TEST` (104884) | one row per person-day-assignment | 2024-12-31 → 2027-01-01 (5,053 rows) | JSON `POST /report/getResultList` |
| First Due `Personnel Shift Distribution` (103415) | roster snapshot | current | JSON `POST /report/getResultList` |
| First Due `getGroups` (`/shift/group/getGroups`) | group membership incl. EMT / Paramedic | current | JSON `GET` |

Raw files stay immutable under `data/raw/`. Everything downstream is generated.

**Deliberately excluded:** First Due incident/unit-hour reports (the module is
essentially unpopulated: 7 rows for 2026, 0 for 2025 and 2021-2024), First Due time
clock (not used; payroll lives elsewhere), and EPCR/patient data (out of scope by
policy: nothing patient-level leaves the department).

## Grain

Three facts, three grains. Do not mix them.

| Table | Grain | Key |
|---|---|---|
| `fact_incident` | one row per incident | `incident_key` |
| `fact_unit_response` | one row per apparatus per incident | `incident_key` + `apparatus_key` |
| `fact_staffing_day` | one row per person per day per assignment | `person_key` + `date_key` + `assignment` |

Derived measures (response time, time on scene, unit-hours committed) are computed at
query time from stored timestamps, never stored as typed values.

## Dimensions

- `dim_date`: calendar, plus a shift column (A/B/C) once the shift rotation is mapped.
- `dim_station`: canonical `ELK` / `GH` / `GB`. Never `Station 2` / `Station 3`.
- `dim_apparatus`: canonical rig codes. **Unresolved:** `E60` vs `ENG60`, `E64` vs
  `ENG64`, `S1` all appear in the source; the same rig carries multiple spellings.
- `dim_incident_type`: NFIRS code plus a two-level taxonomy defined in
  `etl/taxonomy.py`: `family` (Fire, Wildland, EMS, Motor vehicle accident, Backcountry
  rescue, Service, Untyped) and `subtype` (the drill-down the crews actually use). Fire
  drills into Structure fire / Vehicle fire / Alarms / HazMat / Other fire; MVA into With
  injury / No injury / Vehicle extrication / Cleanup (an extrication is an accident with
  entrapment, not a rescue); Wildland into
  Forest-timber / Brush-grass / Vegetation other; Service into Event coverage-standby /
  Public assist / Cancelled-no incident found / Weather & disaster / Other service.
  The classification is **driven by the NFIRS type code**, which is complete in this
  export (three blank rows across 4,828 NFIRS records), with the description used only as
  a fallback for un-coded rows. Two earlier mistakes this replaced: the class was derived
  by keyword-matching the description, so `confined` filed "Cooking fire, confined to
  container" as a backcountry rescue and `water` filed "Water problem, other" the same
  way; and mutual aid was a *class*, when 382 of the 405 rows in it were there because
  mutual aid was given rather than because of the call type. **Mutual aid is a flag**
  (`fact_incident.mutual_aid_given`), never a family.
- `dim_person`: department personnel, keyed on name then reconciled to email.
- `dim_qualification`: qualifier and rank (position-level).
- `dim_certification`: **temporal** credential record per person: certification name,
  `issue_date`, `expiration_date`. This is the source of truth for clinical status.

### Paramedic status: a curated dimension, with provenance

Neither First Due source is sufficient on its own:

- **Groups** (`Medic`/`MEDIC`) are the operational truth, reviewed against the roster by
  Jimmy (9 members), but they carry only *current* membership. Applied backwards it
  overstates Elkhorn paramedic days by 110 (638 vs 528).
- **Certifications** are auditable but **incomplete**: 4 of the 9 group medics have no
  paramedic credential on file (Hannah Baybutt, Julie Youngblood, Nadav Cohen, Zack
  Traylor; three of the four are paid-on-call). Excluding them understates coverage.

Therefore `dim_person_medic` is a small **curated** table, 9 rows, each with an explicit
basis:

| basis | meaning |
|---|---|
| `certification` | `medic_since` derived from a paramedic cert `issue_date`; fully auditable |
| `roster_confirmed` | date supplied by the department ("became a medic in 2024") |
| `estimated` | no date available; carried as a flag, never silently assumed |

Coverage is reported **two ways**: confirmed-only (lower bound) and
confirmed + estimated (upper bound). If the two differ materially, the band is published
rather than a single number. Rows with basis `estimated` are never mixed into a trend
line without a footnote.

Corroborated by certificates, all matching departmental recollection: Katelyn Berman
(2024-03-19), Matt Hansen (2023-02-19), Ricky Williams (2023-08-07), Sarah Driggs
(2024-11-25), Rune Haavik (2024-03-31).

Lapsed credentials are a **data-quality flag with a review list**, not a coverage gate.
Gating on `expiration_date` would drop Elkhorn from 80% to 73% overall and 74% to 60% in
2026, a 50-day swing from one person's unrenewed record.

## Known defects to resolve in the model, not in the data

1. **Station names.** 43% of incidents have no primary station; `Elkhorn Station` and
   `Elkhorn Station - HQ` are the same place; `Station 2` / `Station 3` are obsolete
   naming the department dropped.
2. **Apparatus spellings.** `E60`/`ENG60`, `E64`/`ENG64`: same rig, two codes.
3. **Incident numbers are not unique.** Three schemes coexist (`99-999`, `9999999`, and
   dispatch-generated forms like `BC011326-068`), and **numbers are reused across years**:
   `24-454` is a 2024 NFIRS incident while a 2026 NERIS record also carries `24-454`.
   A single-column primary key silently overwrote 13 real incidents before this was
   caught, so the table uses a surrogate `incident_uid`. Duplicates inside one era are
   deduped by keeping the row with the most populated fields (NFIRS 78, NERIS 7) and are
   logged. Never use the incident number as a join key on its own; pair it with year.
4. **The "113 incidents with no unit rows" finding was wrong**: it was duplicate incident
   numbers in the export (78 keys repeated, 36 rows with a blank number). Every distinct
   incident has unit rows.
5. **Unit-level data stops at the NFIRS era.** Only 32 of 693 incidents in 2026 have
   apparatus rows, so time-on-scene and unit-hours are solid through 2025 and thin in
   2026. State the era whenever a unit-level number is published. 37 apparatus rows carry
   no incident number at all and cannot be joined to anything; they remain in
   `fact_unit_response` with `incident_key IS NULL` and must be excluded from any join.
   Separately, 12 staffing rows share a person, day, assignment and work type but differ
   by qualifier (someone covering two slots); these are real records, not duplicates.
6. **Class regex matched substrings, resolved by moving to type codes.** `ice` matched
   "serv**ice**", classifying 374 "Emergency medical service, other" calls as backcountry
   rescues; word boundaries fixed that one, but `confined` and `water` kept misfiling
   cooking fires and water leaks. Classification now reads the NFIRS code first (see
   `etl/taxonomy.py`), and 2020+ balance is Fire 1,250 / EMS 1,233 / Service 1,026 /
   MVA 178 / Untyped 82 / Wildland 45 / Backcountry rescue 29. The NERIS era is typed
   from Elite's incident list (see "The NERIS era has no incident type in its export"),
   which is why Untyped is now 1.5% rather than 18%.
7. **Sparse lifecycle timestamps.** At incident level `Alarm To Controlled` is 81%
   null, `Alarm To In Service` 76% null, `PSAP To Alarm` 91% null (and 0% before 2021).
   The only reliable incident-level duration is `Alarm To Last Unit Cleared` (98.6%).
   Time on scene therefore comes from the unit grain: `Arrived To Cleared`.
6. **Date formats differ between sources** (`Oct 1, 2025` vs `2026-07-31`).
7. **Lat/long populated for only 5% of incidents**: geographic segmentation is not
   available. Segment by incident type and mutual-aid flag instead.

## Analyst decisions already taken

- **Analysis window starts 2020-01-01.** Incident counts jump 2.19x between 2019 (292)
  and 2020 (431), a reporting-practice change, not workload. Within 2020-2025 growth
  is 431 → 676 (+57%), which is defensible. Charts spanning the boundary must be
  labelled or omitted.
- **Two baskets, not one filter.** Response-time KPIs use in-scope incidents only
  (exclude `Nfpa Mutual Aid Given = yes`, dispatch→arrival > ~25 min, time on scene
  > ~4 h). Excluded work is reported separately as burden (unit-hours consumed). This
  follows the NFPA 1710/1720 convention that out-of-scope work is workload, not
  response performance.
- Wildland deployments and backcountry rescues are real work, not bad data: they are
  segmented out of performance measures and counted as burden.

## Two agencies, one roof

SVFD holds a contract to manage NBCFD (North Blaine County Fire District). Both
jurisdictions' calls count as one operationally, but the data must stay **separable** so
jurisdiction-level questions remain answerable. Every fact row carries `org_code`:

- `SVFD`: Sun Valley Fire Department
- `NBCFD`: North Blaine County Fire District

Source of the flag: **not** the incident number. Crews freely mix the department's
`26-NNN` series with the dispatch-generated number (`BC011326-068`, `NBCFD2026…`), so the
format carries no jurisdiction information: someone using the dispatch number could be
running either agency's call. Only an explicit `SVFD`/`NBCFD` prefix is authoritative.

Resolution order, recorded in `org_source`:

1. `prefix`: incident number starts with `SVFD` or `NBCFD`. Authoritative.
2. `zone_inferred`: from the location zone: `Sun Valley` → SVFD;
   `BCSO North` / `BCSO Central` / `BCSO South` → NBCFD (county). Never presented as
   fact without the flag.
3. `NULL`: left unresolved rather than defaulted. 210 of 720 NERIS rows, mostly blank
   zone (187 in 2026).

Measured 2026 split: SVFD 276, NBCFD 232, unresolved 187.

**Jurisdiction cannot be reconstructed before 2026.** In the NFIRS era the zone field is
blank for 77% of rows (2,414 of 3,141 since 2020), so no reliable jurisdiction signal
exists. Any jurisdiction comparison starts at 2026 and says so.

Default reporting view is combined; jurisdiction filters are a first-class option.

## Stations

| Code | Station | Notes |
|---|---|---|
| `ELK` | Elkhorn | HQ, staffed daily |
| `GH` | Greenhorn | staffed ~24% of days in 2025, ~80% in 2026 |
| `GB` | Griffin Butte | **unstaffed**, 13100 ID-75, Ketchum ID 83340, very low response volume (5 of 690 in 2026) |

Griffin Butte is unstaffed, so its handful of records should never be read as a station
workload signal; it appears in the data mainly as a reporting location.

## Reporting completeness lag (do not read the last months as decline)

Reports are written after the fact, and some are outstanding for months. Recent months
are therefore always under-counted, and the shortfall is largest in the newest month.
Evidence: 2026 runs 22/13/11/10 per month for Jan–Apr, then 58/78/105/103/71/93 from May,
against a 2025 baseline of ~56/month, a shape that matches reporting catch-up, not a
collapse in demand.

Rules:

- Never treat the trailing 3 months as a trend without stating the cut-off date.
- Trend charts carry an explicit "data as of" annotation and a shaded incomplete tail.
- When a prior pull exists, compare the same month across pulls to measure the lag
  directly.

## NERIS duplicates every timestamp (and that rescues some of them)

NERIS carries **two parallel timestamp families** for the same event:

- dispatch side: `Dispatch Alarm Date Time (itfiDispatch.001)`,
  `Dispatch Arrival Date Time (itfiDispatch.002)`, `Dispatch Incident Clear Date Time (fiDispatch.01)`
- incident side: `Incident Alarm Date Time (itfiIncident.002)`,
  `Incident Arrival Date Time (itfiIncident.003)`, `Incident In Service Date Time (itfiIncident.005)`,
  `Incident Date Time (itfiIncident.004)`

Crews populate one family or the other, not both. Measured on the 2026 NERIS pull:
240 of 737 rows had no dispatch alarm time, and **70 of those were recoverable** from the
incident-side alarm field. The remaining 170 have no alarm time on either side (161 of
them carry other data and are usable for duration measures, just not for response time).

Rule: coalesce dispatch-first, incident-side-second, and record which side supplied each
timestamp in a `*_source` column so a metric can be audited back to its origin.

## Geography: offline geocoding, with stated precision

The incident export carries 248 coordinates (4.5%) but an address on 98% of rows, so
location comes from matching street names against a local reference rather than from the
source itself.

- **Reference:** `data/raw/osm_streets.csv` (highway centrelines with name *and* route
  `ref`) and `data/raw/osm_places.csv` (named buildings and amenities), pulled once from
  Overpass in `etl/pull/osm_streets.py`. Nothing is sent to a geocoding service - the
  addresses never leave the machine.
- **Matching** (`etl/load_geo.py`): case and punctuation normalised, trailing street
  types and directional prefixes stripped on both sides, leading house numbers removed
  from the street field ("113 ANGANI WAY"), and route keys built from `ref` because the
  road crews radio as Highway 75 is named "Idaho Medal of Honor Highway" in OSM. Street
  names beat building names on a tie.
- **Precision is stored per incident** in `geo_source`:
  `reported_point` (a coordinate the crew actually recorded), `street_centroid` (middle
  of the matched street), `street_centroid_broad` (street longer than 2 km - the centroid
  of a valley-long highway is a fair marker for "somewhere on that road" and nothing
  more). Nothing is interpolated to a house number: a street-level map is defensible, a
  fabricated address is not.
- **Unmatched streets** go to `data/clean/geo_unmatched.csv` with volumes, the same
  review discipline as station and apparatus mappings. 708 incidents remain unmatched
  across 474 distinct street strings; the largest are private developments OSM does not
  carry at all. The next step there is a curated `data/curated/geo_aliases.csv`
  (name -> point or street), supplied from local knowledge rather than guessed.
- **The map is count-based, not a density surface.** Points are aggregated into ~150 m
  cells and coloured by the number of incidents in the cell, so the legend reads in
  calls. Plotly's density trace normalises its surface to 0-1 in the browser, which
  cannot be quoted and misleads a reader about volume.
- **The NERIS era has no address in its CSV export**, but Elite's NERIS Fire incident
  list carries one (`NerisFullAddress`). `etl/pull/elite_neris_addresses.py` scrapes that
  list once into `data/raw/elite_neris_addresses.csv`, and `load_geo.py` backfills
  street/city from it before matching. That closed 2026 from 2% placed to 79%.
- Address strings arrive dirty and are cleaned deterministically: a trailing "City of",
  a leading mile marker ("MM 143 STATE HIGHWAY 75"), and "A Road / B Road" intersections
  (first street wins). Each rule is visible in `split_address()`.

## The NERIS era has no incident type in its export

The NERIS-era CSV's `Incident Type` column returns the literal string `Fire NERIS` on all
737 rows, and `Dispatch Incident Code` is blank on 719 of them, so the export alone cannot
say what those calls were.

The data exists in Elite. The NERIS Fire incident list shows `Primary Incident Type` per
incident in the standardised three-part NERIS vocabulary ("Medical - Injury / Trauma -
Fall", "Public Service - Alarms (Non Medical) - Fire / Smoke Alarm"), and
`etl/pull/elite_neris_incidents.py` scrapes it once into
`data/raw/elite_neris_incidents.csv` alongside the address.

`etl/taxonomy.py::classify_neris()` maps that vocabulary onto the same six families, by
category rather than by guessing at free text: Medical to EMS; Fire - Structure to
Structure fire; Fire - Outside - Vegetation to Wildland; Rescue - Transportation to MVA
with extrication; Hazardous Situation to HazMat, except motor-vehicle collisions (MVA)
and investigations (Alarms); Public Service - Alarms to Alarms; No Emergency - False Alarm
to Alarms, No Emergency - Cancelled to the cancelled bucket. All 69 distinct strings in the
department's vocabulary map cleanly.

Result: **83 incidents remain `Untyped` (1.5% of the series)**, all of them NERIS-era
records where the crew recorded no type at all, or which never reached the incident list.
The family stays in the taxonomy so that gap stays visible instead of inflating Fire.

## Metric definitions

Each metric gets a written definition and a unit test. Draft set:

| Metric | Definition | Grain |
|---|---|---|
| `demand` | count of in-scope incidents | incident |
| `response_time` | alarm → first unit arrival | incident |
| `turnout_time` | dispatch → en route | unit |
| `travel_time` | en route → arrival | unit |
| `time_on_scene` | arrived → cleared | unit |
| `unit_hours` | cleared − dispatch, summed | unit |
| `coverage_days` | days with ≥1 medic-qualified person on an assignment | staffing |

### Coverage rule (station coverage)

- **Included:** assignments `Elkhorn` and `Greenhorn`; work types `Regular`, `POC Shift`,
  `Trade ON`, `Overtime`. Paid-on-call medics appear only as `POC Shift` rows (Hannah 13,
  Nadav 9, Zack 13 over the window); filtering to `Regular` alone silently deletes them.
- **Excluded:** `Training` / `Fire Academy` (not coverage), and non-station work:
  `Event Staffing`, `EMS Standby`, `Wildland Deployment`, `Special Assignment`.
- **Ambiguous, excluded conservative:** `Day Shift` (37), `Probie- Day Shift` (71),
  `POC Shift 24hr` (48), probably station coverage under other names. Report the
  conservative figure and note the alternative.
- **Actual vs planned:** anything after the pull date is published schedule, not worked
  days. Always carry `is_actual` and never let planned coverage into a performance
  measure.

## Repo layout

```
data/raw/        immutable source pulls (Elite CSVs, First Due JSON)
data/clean/      normalised tables
docs/            this file, metric definitions, findings
etl/             loaders and identity resolution
app/             dashboard
```
