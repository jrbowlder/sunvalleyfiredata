-- Card: Call types, yearly
-- Definition: incidents by call family and year, the table behind the stacked chart. Untyped
-- stays visible: it is where a data-entry gap would first show up.
SELECT COALESCE(NULLIF(incident_class, ''), 'Untyped') AS call_family,
       SUM(CASE WHEN year = 2020 THEN 1 ELSE 0 END) AS "2020",
       SUM(CASE WHEN year = 2021 THEN 1 ELSE 0 END) AS "2021",
       SUM(CASE WHEN year = 2022 THEN 1 ELSE 0 END) AS "2022",
       SUM(CASE WHEN year = 2023 THEN 1 ELSE 0 END) AS "2023",
       SUM(CASE WHEN year = 2024 THEN 1 ELSE 0 END) AS "2024",
       SUM(CASE WHEN year = 2025 THEN 1 ELSE 0 END) AS "2025",
       SUM(CASE WHEN year = 2026 THEN 1 ELSE 0 END) AS "2026",
       COUNT(*) AS total
FROM svfd.demo.fact_incident
WHERE is_actual = 1
  AND CAST(substr(incident_dt, 1, 10) AS DATE) >= COALESCE(CAST({{start_date}} AS DATE), DATE '2020-01-01')
  AND CAST(substr(incident_dt, 1, 10) AS DATE) <= COALESCE(CAST({{end_date}} AS DATE), DATE '2099-12-31')
  AND year IS NOT NULL
GROUP BY 1
ORDER BY total DESC;
