-- Card: KPI strip
-- One row, four numbers, all scoped by the From/To inputs so the tiles can never disagree with
-- the charts below them. Each tile reads a column from this single result.
-- Percentiles are nearest-rank over the in-scope basket, the same convention as every card.
WITH inc AS (
  SELECT incident_class, mutual_aid_given, alarm_to_arrival_min
  FROM svfd.demo.fact_incident
  WHERE is_actual = 1
    AND CAST(substr(incident_dt, 1, 10) AS DATE) >= COALESCE(CAST({{start_date}} AS DATE), DATE '2020-01-01')
    AND CAST(substr(incident_dt, 1, 10) AS DATE) <= COALESCE(CAST({{end_date}} AS DATE), DATE '2099-12-31')
),
scoped AS (
  SELECT *,
         CASE WHEN COALESCE(mutual_aid_given, '') = ''
               AND COALESCE(incident_class, '') <> 'Wildland'
               AND alarm_to_arrival_min IS NOT NULL
               AND alarm_to_arrival_min <= 25
              THEN 1 ELSE 0 END AS in_scope
  FROM inc
),
timed AS (
  SELECT alarm_to_arrival_min AS v,
         ROW_NUMBER() OVER (ORDER BY alarm_to_arrival_min) AS rn,
         COUNT(*)     OVER ()                             AS n
  FROM scoped
  WHERE in_scope = 1
)
SELECT (SELECT COUNT(*) FROM scoped)                                AS incidents,
       (SELECT MAX(CASE WHEN rn = CAST(FLOOR(0.50 * (n - 1)) AS INT) + 1 THEN v END)
          FROM timed)                                               AS p50_min,
       (SELECT MAX(CASE WHEN rn = CAST(FLOOR(0.90 * (n - 1)) AS INT) + 1 THEN v END)
          FROM timed)                                               AS p90_min,
       ROUND(100.0 * SUM(in_scope) / COUNT(*), 1)                   AS pct_in_scope
FROM scoped;
