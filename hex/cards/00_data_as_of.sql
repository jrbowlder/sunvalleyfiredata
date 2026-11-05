-- Card: Data as of
-- Definition: the newest incident date in the warehouse. Report it next to every trend
-- chart: reports are written after the fact, so the newest months are always incomplete.
SELECT MAX(substr(incident_dt, 1, 10)) AS data_as_of,
       MAX(year)                        AS latest_year,
       COUNT(*)                         AS incidents_on_hand
FROM svfd.demo.fact_incident
WHERE is_actual = 1;
