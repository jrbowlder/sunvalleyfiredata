-- Card: Call-type mix by year
-- Definition: two-level taxonomy driven by the NFIRS type code, so mutual aid stays a flag
-- rather than a call type. Untyped is kept visible instead of being hidden: it is 1 to 2
-- percent of records and it is where a data-entry gap would show up.
SELECT CAST(year AS VARCHAR) AS year,
       COALESCE(NULLIF(incident_class, ''), 'Untyped') AS call_family,
       COUNT(*)                                        AS incidents
FROM svfd.demo.fact_incident
WHERE is_actual = 1
  AND CAST(substr(incident_dt, 1, 10) AS DATE) >= COALESCE(CAST({{start_date}} AS DATE), DATE '2020-01-01')
  AND CAST(substr(incident_dt, 1, 10) AS DATE) <= COALESCE(CAST({{end_date}} AS DATE), DATE '2099-12-31')
  AND year IS NOT NULL
GROUP BY 1, 2
ORDER BY 1, 3 DESC;
