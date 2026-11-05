-- Card: Response time, monthly p50 and p90
-- Definition: alarm to first unit arrival, in-scope incidents only (no mutual aid given,
-- not a wildland deployment, arrival inside 25 minutes). Nearest-rank percentiles, the same
-- convention as the analysis scripts, so the numbers match the printed report to the decimal.
-- Months under 10 incidents are dropped rather than plotted as a spike.
WITH b AS (
  SELECT strptime(substr(incident_dt, 1, 7), '%Y-%m') AS month,
         alarm_to_arrival_min AS v
  FROM svfd.demo.fact_incident
  WHERE is_actual = 1
    AND CAST(substr(incident_dt, 1, 10) AS DATE) >= COALESCE(CAST({{start_date}} AS DATE), DATE '2020-01-01')
    AND CAST(substr(incident_dt, 1, 10) AS DATE) <= COALESCE(CAST({{end_date}} AS DATE), DATE '2099-12-31')
    AND incident_dt IS NOT NULL
    AND COALESCE(mutual_aid_given, '') = ''
    AND COALESCE(incident_class, '') <> 'Wildland'
    AND alarm_to_arrival_min IS NOT NULL
    AND alarm_to_arrival_min <= 25
),
ranked AS (
  SELECT month, v,
         ROW_NUMBER() OVER (PARTITION BY month ORDER BY v) AS rn,
         COUNT(*)     OVER (PARTITION BY month)           AS n
  FROM b
)
SELECT month,
       MAX(n)                                                       AS incidents,
       ROUND(MAX(CASE WHEN rn = CAST(FLOOR(0.50 * (n - 1)) AS INT) + 1 THEN v END), 1) AS p50,
       ROUND(MAX(CASE WHEN rn = CAST(FLOOR(0.90 * (n - 1)) AS INT) + 1 THEN v END), 1) AS p90
FROM ranked
GROUP BY month
HAVING MAX(n) >= 10
ORDER BY month;
