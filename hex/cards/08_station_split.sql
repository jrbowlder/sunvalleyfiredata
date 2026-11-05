-- Card: Incidents by station and staffing
-- Definition: the station recorded on the incident, split by whether that station had a crew
-- that day. Greenhorn is staffed part time, so "Greenhorn" alone hides the thing the chief
-- actually asks about: what shows up when nobody is posted there. Griffin Butte has no posted
-- crew at all in this period, so it carries no staffed/unstaffed split.
-- Unknown means no station was recorded on the report; it is shown, never dropped.
WITH inc AS (
  SELECT station_code, substr(incident_dt, 1, 10) AS day
  FROM svfd.demo.fact_incident
  WHERE is_actual = 1
  AND CAST(substr(incident_dt, 1, 10) AS DATE) >= COALESCE(CAST({{start_date}} AS DATE), DATE '2020-01-01')
  AND CAST(substr(incident_dt, 1, 10) AS DATE) <= COALESCE(CAST({{end_date}} AS DATE), DATE '2099-12-31')
),
crew AS (
  SELECT station_code, work_date, SUM(headcount) AS headcount
  FROM svfd.demo.v_station_day
  WHERE is_actual = 1
  GROUP BY 1, 2
),
named AS (
  SELECT station_code, station_name FROM svfd.demo.dim_station
)
SELECT CASE
         WHEN inc.station_code IS NULL THEN 'Unknown'
         WHEN crew.headcount > 0 THEN n.station_name || ' (staffed)'
         WHEN crew.headcount = 0 THEN n.station_name || ' (unstaffed)'
         ELSE COALESCE(n.station_name, inc.station_code)
       END AS station,
       COUNT(*) AS incidents
FROM inc
LEFT JOIN crew ON crew.station_code = inc.station_code AND crew.work_date = inc.day
LEFT JOIN named n ON n.station_code = inc.station_code
GROUP BY 1
ORDER BY 2 DESC;
