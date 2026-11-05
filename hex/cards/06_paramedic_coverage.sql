-- Card: Paramedic coverage, monthly (Elkhorn)
-- Definition: share of actual station-days at Elkhorn with a paramedic-qualified person
-- assigned. Only certification and roster-confirmed people count; estimates are excluded
-- rather than blended in. Elkhorn only, because a medic rostered to Greenhorn is a
-- credential the department holds, not a service it runs.
SELECT strptime(substr(work_date, 1, 7), '%Y-%m') AS month,
       COUNT(*)                 AS station_days,
       SUM(has_paramedic)       AS days_with_medic,
       ROUND(100.0 * SUM(has_paramedic) / COUNT(*), 1) AS "pct of days"
FROM svfd.demo.v_station_day
WHERE is_actual = 1
  AND CAST(substr(work_date, 1, 10) AS DATE) >= COALESCE(CAST({{start_date}} AS DATE), DATE '2020-01-01')
  AND CAST(substr(work_date, 1, 10) AS DATE) <= COALESCE(CAST({{end_date}} AS DATE), DATE '2099-12-31')
  AND station_code = 'ELK'
GROUP BY 1
ORDER BY 1;
