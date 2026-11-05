-- Card: Story metrics
-- One row, every number the narrative sentences quote. The prose is generated from this row,
-- so a data refresh rewrites the story instead of contradicting it.
-- Percentiles are nearest-rank over the in-scope basket, matching every other card.
WITH inc AS (
  SELECT year, incident_class, mutual_aid_given, alarm_to_arrival_min,
         alarm_to_cleared_min, incident_dt, station_code
  FROM svfd.demo.fact_incident
  WHERE is_actual = 1
),
bounds AS (
  -- Compare like with like: the newest year is part-year, so the story uses the last year
  -- with twelve months of records.
  SELECT MIN(year) AS first_year,
         MAX(CASE WHEN months = 12 THEN year END) AS last_year
  FROM (
    SELECT year, COUNT(DISTINCT substr(incident_dt, 1, 7)) AS months
    FROM inc WHERE year IS NOT NULL AND incident_dt IS NOT NULL
    GROUP BY 1
  )
),
demand AS (
  SELECT year, COUNT(*) AS n FROM inc WHERE year IS NOT NULL GROUP BY 1
),
perf AS (
  SELECT year, alarm_to_arrival_min AS v
  FROM inc
  WHERE COALESCE(mutual_aid_given, '') = ''
    AND COALESCE(incident_class, '') <> 'Wildland'
    AND alarm_to_arrival_min IS NOT NULL
    AND alarm_to_arrival_min <= 25
),
ranked AS (
  SELECT year, v,
         ROW_NUMBER() OVER (PARTITION BY year ORDER BY v) AS rn,
         COUNT(*)     OVER (PARTITION BY year)           AS n
  FROM perf
),
percentiles AS (
  SELECT year,
         MAX(CASE WHEN rn = CAST(FLOOR(0.50 * (n - 1)) AS INT) + 1 THEN v END) AS p50,
         MAX(CASE WHEN rn = CAST(FLOOR(0.90 * (n - 1)) AS INT) + 1 THEN v END) AS p90
  FROM ranked GROUP BY year
),
scene AS (
  SELECT year,
         alarm_to_cleared_min - alarm_to_arrival_min AS v
  FROM inc
  WHERE alarm_to_cleared_min IS NOT NULL AND alarm_to_arrival_min IS NOT NULL
    AND alarm_to_cleared_min >= alarm_to_arrival_min
),
scene_ranked AS (
  SELECT year, v,
         ROW_NUMBER() OVER (PARTITION BY year ORDER BY v) AS rn,
         COUNT(*)     OVER (PARTITION BY year)           AS n
  FROM scene
),
scene_p50 AS (
  SELECT year,
         MAX(CASE WHEN rn = CAST(FLOOR(0.50 * (n - 1)) AS INT) + 1 THEN v END) AS p50
  FROM scene_ranked GROUP BY year
),
crew AS (
  SELECT s.work_date, s.station_code, SUM(s.headcount) AS headcount
  FROM svfd.demo.v_station_day s
  WHERE s.is_actual = 1
  GROUP BY 1, 2
),
per_station_day AS (
  SELECT CASE WHEN c.headcount >= 3 THEN 'three' ELSE 'two' END AS crew_band,
         COUNT(*) AS station_days,
         SUM(COALESCE(d.calls, 0)) AS calls
  FROM crew c
  LEFT JOIN (
    SELECT substr(incident_dt, 1, 10) AS day, station_code, COUNT(*) AS calls
    FROM inc WHERE incident_dt IS NOT NULL GROUP BY 1, 2
  ) d ON d.day = c.work_date AND d.station_code = c.station_code
  WHERE c.headcount > 0
  GROUP BY 1
),
station_days AS (
  -- v_station_day only carries rows for days with a schedule entry, so a rollup over rows
  -- always reads 100% staffed. Divide by the calendar instead.
  SELECT s.station_code,
         s.year,
         CASE WHEN s.year % 4 = 0 THEN 366 ELSE 365 END             AS days,
         COUNT(DISTINCT CASE WHEN s.headcount > 0 THEN s.work_date END) AS staffed_days
  FROM svfd.demo.v_station_day s
  WHERE s.is_actual = 1 AND s.station_code = 'GH'
  GROUP BY 1, 2
),
greenhorn_calls AS (
  SELECT year,
         COUNT(*)                                   AS calls,
         COUNT(DISTINCT substr(incident_dt, 1, 7))  AS months
  FROM inc WHERE station_code = 'GH' AND year IS NOT NULL GROUP BY 1
)
SELECT b.first_year,
       b.last_year,
       d_first.n                                        AS demand_first,
       d_last.n                                         AS demand_last,
       ROUND(100.0 * (d_last.n - d_first.n) / d_first.n, 0) AS demand_pct_change,
       p_first.p50                                      AS p50_first,
       p_first.p90                                      AS p90_first,
       p_last.p50                                       AS p50_last,
       p_last.p90                                       AS p90_last,
       s_first.p50                                      AS scene_first,
       s_last.p50                                       AS scene_last,
       (SELECT ROUND(100.0 * staffed_days / days, 0) FROM station_days WHERE year = 2025)
                                                        AS gh_staffed_pct_2025,
       (SELECT ROUND(100.0 * staffed_days / days, 0) FROM station_days WHERE year = 2026)
                                                        AS gh_staffed_pct_2026,
       (SELECT calls  FROM greenhorn_calls WHERE year = 2025) AS gh_calls_2025,
       (SELECT months FROM greenhorn_calls WHERE year = 2025) AS gh_months_2025,
       (SELECT calls  FROM greenhorn_calls WHERE year = 2026) AS gh_calls_2026,
       (SELECT months FROM greenhorn_calls WHERE year = 2026) AS gh_months_2026,
       (SELECT MAX(substr(incident_dt, 1, 10)) FROM inc WHERE incident_dt IS NOT NULL)
                                                        AS data_as_of
FROM bounds b
JOIN demand d_first ON d_first.year = b.first_year
JOIN demand d_last  ON d_last.year  = b.last_year
JOIN percentiles p_first ON p_first.year = b.first_year
JOIN percentiles p_last  ON p_last.year  = b.last_year
LEFT JOIN scene_p50 s_first ON s_first.year = b.first_year
LEFT JOIN scene_p50 s_last  ON s_last.year  = b.last_year;
