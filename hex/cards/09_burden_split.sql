-- Card: Burden, in scope versus out of scope
-- Definition: every actual incident, split by whether it lands in the response basket. Out of
-- scope work is not a failure: it is mutual aid given, wildland deployment, or an arrival past
-- 25 minutes. It is counted here as workload so it is never folded into performance.
SELECT CASE WHEN m.months < 12
            THEN CAST(f.year AS VARCHAR) || ' (' || m.months || ' mo)'
            ELSE CAST(f.year AS VARCHAR) END AS year,
       SUM(CASE WHEN COALESCE(f.mutual_aid_given, '') = '' AND COALESCE(f.incident_class, '') <> 'Wildland' AND f.alarm_to_arrival_min IS NOT NULL AND f.alarm_to_arrival_min <= 25 THEN 1 ELSE 0 END) AS in_scope,
       SUM(CASE WHEN COALESCE(f.mutual_aid_given, '') = '' AND COALESCE(f.incident_class, '') <> 'Wildland' AND f.alarm_to_arrival_min IS NOT NULL AND f.alarm_to_arrival_min <= 25 THEN 0 ELSE 1 END) AS out_of_scope
FROM svfd.demo.fact_incident f
JOIN (SELECT year, COUNT(DISTINCT substr(incident_dt, 1, 7)) AS months
      FROM svfd.demo.fact_incident
      WHERE is_actual = 1 AND incident_dt IS NOT NULL
      GROUP BY 1) m
  ON m.year = f.year
WHERE f.is_actual = 1
  AND CAST(substr(f.incident_dt, 1, 10) AS DATE) >= COALESCE(CAST({{start_date}} AS DATE), DATE '2020-01-01')
  AND CAST(substr(f.incident_dt, 1, 10) AS DATE) <= COALESCE(CAST({{end_date}} AS DATE), DATE '2099-12-31')
  AND f.year IS NOT NULL
GROUP BY f.year, m.months
ORDER BY f.year;
