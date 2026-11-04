# Findings — synthetic build

> **These results come from generated data.** They describe the behaviour of the synthetic
> dataset shipped with the public repo, not the operations of any real department. The
> distributions are plausible by construction (`etl/synthetic.py`), and the point of this
> document is that every number below is reproducible from the code in this repository.

Window 2020-01-01 onward (see "Why 2020"). Numbers regenerated from
`data/synthetic/warehouse.db` by `analysis/01_demand_performance.py` and
`analysis/02_demand_vs_coverage.py`:

```bash
export SVFD_DB=data/synthetic/warehouse.db
python3 analysis/01_demand_performance.py
python3 analysis/02_demand_vs_coverage.py
```

## Why 2020

The generated series starts in 2013 to mirror the real pull, where a reporting-practice
change steps incident counts up 2.19x in 2020 (the department was volunteer-run with poor
logging before that). Within the clean window the growth is defensible and the boundary is
excluded from every trend.

## 1. Demand grew 57% over five years, then levelled

| Year | Incidents | Per day | YoY |
|---|---|---|---|
| 2020 | 429 | 1.17 | — |
| 2021 | 470 | 1.29 | +10% |
| 2022 | 512 | 1.40 | +9% |
| 2023 | 553 | 1.52 | +8% |
| 2024 | 596 | 1.63 | +8% |
| 2025 | 675 | 1.85 | +13% |
| 2026 (Jan–Oct) | 554 | 1.82 | −1% per day |

**+57% from 2020 to 2025.** The 2026 figure covers ten months and is generated with the
real pull's reporting-lag month shape, so the low early months are catch-up, not calm —
but the per-day rate is roughly flat against 2025 rather than the +25% the private dataset
shows. That is a property of the generator's month counts, not a finding.

## 2. Response performance is stable and slightly improving

In-scope incidents only (no mutual aid given, arrival ≤ 25 min, not wildland):

| Year | n | p50 | p90 | p95 |
|---|---|---|---|---|
| 2020 | 386 | 7.5 | 13.4 | 15.3 |
| 2021 | 433 | 7.3 | 13.9 | 17.2 |
| 2022 | 451 | 7.3 | 13.5 | 16.0 |
| 2023 | 482 | 7.5 | 14.1 | 16.5 |
| 2024 | 525 | 8.0 | 13.8 | 16.1 |
| 2025 | 609 | 6.7 | 12.4 | 14.8 |
| 2026 | 424 | 6.8 | 11.8 | 13.8 |

p90 stays between 11.8 and 14.1 minutes across seven years while volume rises 57%. The
in-scope basket is 89% of incidents, so these percentiles are not cherry-picked survivors.

## 3. Time on scene is stable

Unit grain, arrived → cleared, outliers beyond 4 h excluded:

| Year | Units | p50 | p90 |
|---|---|---|---|
| 2020 | 726 | 15.5 | 33.9 |
| 2021 | 767 | 15.6 | 33.0 |
| 2022 | 821 | 15.4 | 36.6 |
| 2023 | 901 | 16.2 | 36.7 |
| 2024 | 1001 | 16.6 | 34.9 |
| 2025 | 1109 | 15.5 | 33.6 |
| 2026 | 500 | 16.3 | 41.4 |

Median scene time holds at 15–17 minutes. The 2026 p90 is higher on a smaller sample
(500 unit rows across 55% of incidents), so it should be read as noise.

## 4. Workload per unit of demand

| Year | Unit rows | Unit-hours | Hours/day |
|---|---|---|---|
| 2020 | 751 | 495.6 | 1.35 |
| 2021 | 794 | 522.7 | 1.43 |
| 2022 | 844 | 542.1 | 1.49 |
| 2023 | 922 | 568.5 | 1.56 |
| 2024 | 1029 | 671.1 | 1.83 |
| 2025 | 1139 | 682.2 | 1.87 |
| 2026 | 520 | 374.6 | 1.03 |

Unit-hours committed rose 38% from 2020 to 2025 — less than the 57% rise in incidents,
because the average incident got slightly cheaper to run. **2026 is not comparable**: it
has 55% of incidents carrying apparatus rows, so the drop is coverage, not a lighter
workload. Never publish a unit-level number for 2026 without that sentence.

## 5. Staffing does not track demand

Mean calls per station-day by crew size, 2025-2026:

| Crew on duty | Station-days | Mean calls/day |
|---|---|---|
| 1 | 6 | 1.33 |
| 3 | 336 | 1.76 |
| 4+ | 761 | 1.86 |

Nearly flat from three crew upward. Staffing is set by the rotation; call volume arrives
independently of it, so added demand shows up as load per crew rather than as a
scheduling trigger.

## 6. Greenhorn's staffing changed during the period

| Year | Calls | Staffed share of days | p50 staffed | p50 unstaffed |
|---|---|---|---|---|
| 2025 | 170 | 53% | 6.6 | 8.6 |
| 2026 | 129 | 75% | 7.0 | 9.3 |

Greenhorn went from staffed about half the days to three-quarters, and its response
penalty on unstaffed days is visible and consistent (≈2 minutes at p50). Unlike the
private dataset, the unstaffed sample here is large enough to be more than a lead —
112 calls across the two years.

## 7. Paramedic on duty at Elkhorn

Medical-type incidents attributed to Elkhorn, 2025-2026:

| Paramedic on duty | Calls | p50 | p90 |
|---|---|---|---|
| Yes | 140 | 6.4 | 12.7 |
| No | 62 | 7.1 | 15.3 |

A paramedic on duty is associated with a 0.7-minute better median and a 2.6-minute better
p90. The sample is larger than the private dataset's (which rested on 16 calls), so this
reads as a real relationship **in the generated data** — it is a consequence of how the
generator models staffing, not evidence about any department.

## Caveats that travel with these numbers

1. **It is synthetic.** Reproducible, internally consistent, and not about a real agency.
2. **Reporting lag.** The generator reproduces the incomplete tail; the last months of
   2026 must never be read as decline.
3. **2026 unit coverage is 55% by design**, against ~5% in the real pull. Unit-level 2026
   figures are usable here but are not comparable to the private dataset.
4. **The NERIS era is typed by design.** The real pull leaves `Dispatch Incident Code`
   blank on 98% of rows; here 97% carry a code so call-type views have content.
5. **Coverage data starts 2024-12-31**, so nothing in sections 5–7 extends backwards.
6. **Station attribution is the reporting station**, not necessarily the responding one.
7. **Location is street-level.** About 90% of incidents are placed at a street centroid;
   the rest are deliberately unmatched or have no address.
