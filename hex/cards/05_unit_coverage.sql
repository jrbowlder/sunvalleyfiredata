-- Card: Apparatus record coverage by year
-- Definition: share of incidents that carry at least one apparatus row. Unit-level measures
-- (time on scene per unit, unit-hours) are only publishable where this is high; a year at 2%
-- looks like a workload collapse when it is missing data.
WITH i AS (
  SELECT year, incident_key
  FROM svfd.demo.fact_incident
  WHERE is_actual = 1 AND year IS NOT NULL
),
u AS (
  SELECT DISTINCT incident_key FROM svfd.demo.fact_unit_response
)
SELECT CAST(i.year AS VARCHAR) AS year,
       COUNT(*)                AS incidents,
       COUNT(u.incident_key)                             AS with_unit_rows,
       ROUND(100.0 * COUNT(u.incident_key) / COUNT(*), 1) AS "pct with records"
FROM i
LEFT JOIN u ON u.incident_key = i.incident_key
GROUP BY i.year
ORDER BY i.year;
