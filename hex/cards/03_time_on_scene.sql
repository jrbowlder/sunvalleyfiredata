-- Card: Time on scene, monthly p50
-- Definition: arrival to last unit cleared, incident grain, so it spans every year even
-- where apparatus records are thin. Calls still open after 4 hours are excluded as data
-- problems rather than plotted as long scenes.
WITH b AS (
  SELECT strptime(substr(incident_dt, 1, 7), '%Y-%m') AS month,
         alarm_to_cleared_min - alarm_to_arrival_min AS v
  FROM svfd.demo.fact_incident
  WHERE is_actual = 1
    AND CAST(substr(incident_dt, 1, 10) AS DATE) >= COALESCE(CAST({{start_date}} AS DATE), DATE '2020-01-01')
    AND CAST(substr(incident_dt, 1, 10) AS DATE) <= COALESCE(CAST({{end_date}} AS DATE), DATE '2099-12-31')
    AND incident_dt IS NOT NULL
    AND alarm_to_cleared_min IS NOT NULL
    AND alarm_to_arrival_min IS NOT NULL
    AND alarm_to_cleared_min >= alarm_to_arrival_min
    AND (alarm_to_cleared_min - alarm_to_arrival_min) <= 240
),
ranked AS (
  SELECT month, v,
         ROW_NUMBER() OVER (PARTITION BY month ORDER BY v) AS rn,
         COUNT(*)     OVER (PARTITION BY month)           AS n
  FROM b
)
SELECT month,
       MAX(n) AS incidents,
       ROUND(MAX(CASE WHEN rn = CAST(FLOOR(0.50 * (n - 1)) AS INT) + 1 THEN v END), 1) AS p50
FROM ranked
GROUP BY month
HAVING MAX(n) >= 10
ORDER BY month;
