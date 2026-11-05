-- Card: Demand, monthly
-- Definition: incidents in the month. Actual records only, so published future shifts and
-- placeholder rows never inflate the count. The trailing months are incomplete; pair this
-- with the Data as of card rather than reading the tail as a decline.
SELECT strptime(substr(incident_dt, 1, 7), '%Y-%m') AS month,
       COUNT(*)                                    AS incidents
FROM svfd.demo.fact_incident
WHERE is_actual = 1
  AND CAST(substr(incident_dt, 1, 10) AS DATE) >= COALESCE(CAST({{start_date}} AS DATE), DATE '2020-01-01')
  AND CAST(substr(incident_dt, 1, 10) AS DATE) <= COALESCE(CAST({{end_date}} AS DATE), DATE '2099-12-31')
  AND incident_dt IS NOT NULL
GROUP BY 1
ORDER BY 1;
