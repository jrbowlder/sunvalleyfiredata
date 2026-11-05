-- Card: Why incidents are out of scope
-- Definition: the reasons, counted independently, so an incident with both mutual aid and a
-- slow arrival appears under both. This is the table a chief reads when the in-scope share
-- looks low and they want to know whether that is workload or a problem.
SELECT 'mutual aid given' AS reason,
       COUNT(*) AS incidents
FROM svfd.demo.fact_incident
WHERE is_actual = 1
  AND CAST(substr(incident_dt, 1, 10) AS DATE) >= COALESCE(CAST({{start_date}} AS DATE), DATE '2020-01-01')
  AND CAST(substr(incident_dt, 1, 10) AS DATE) <= COALESCE(CAST({{end_date}} AS DATE), DATE '2099-12-31')
  AND COALESCE(mutual_aid_given, '') <> ''
UNION ALL
SELECT 'wildland deployment', COUNT(*)
FROM svfd.demo.fact_incident
WHERE is_actual = 1
  AND CAST(substr(incident_dt, 1, 10) AS DATE) >= COALESCE(CAST({{start_date}} AS DATE), DATE '2020-01-01')
  AND CAST(substr(incident_dt, 1, 10) AS DATE) <= COALESCE(CAST({{end_date}} AS DATE), DATE '2099-12-31')
  AND COALESCE(incident_class, '') = 'Wildland'
UNION ALL
SELECT 'arrival over 25 min', COUNT(*)
FROM svfd.demo.fact_incident
WHERE is_actual = 1
  AND CAST(substr(incident_dt, 1, 10) AS DATE) >= COALESCE(CAST({{start_date}} AS DATE), DATE '2020-01-01')
  AND CAST(substr(incident_dt, 1, 10) AS DATE) <= COALESCE(CAST({{end_date}} AS DATE), DATE '2099-12-31')
  AND alarm_to_arrival_min IS NOT NULL AND alarm_to_arrival_min > 25
ORDER BY incidents DESC;
