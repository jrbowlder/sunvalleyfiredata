-- Card: Long calls, share over four hours
-- Definition: calls where the crew was on scene four hours or more, as a share of calls
-- with a usable clear time. One 6-hour call ties up a crew for a shift segment; the count
-- matters less than how often it happens.
WITH b AS (
  SELECT CAST(year AS VARCHAR) AS year,
         alarm_to_cleared_min - alarm_to_arrival_min AS on_scene
  FROM svfd.demo.fact_incident
  WHERE is_actual = 1
    AND CAST(substr(incident_dt, 1, 10) AS DATE) >= COALESCE(CAST({{start_date}} AS DATE), DATE '2020-01-01')
    AND CAST(substr(incident_dt, 1, 10) AS DATE) <= COALESCE(CAST({{end_date}} AS DATE), DATE '2099-12-31')
    AND year IS NOT NULL
    AND alarm_to_cleared_min IS NOT NULL
    AND alarm_to_arrival_min IS NOT NULL
    AND alarm_to_cleared_min >= alarm_to_arrival_min
)
SELECT CASE WHEN m.months < 12 THEN b.year || ' (' || m.months || ' mo)' ELSE b.year END AS year,
       COUNT(*)                                              AS calls,
       SUM(CASE WHEN on_scene >= 240 THEN 1 ELSE 0 END)        AS over_four_hours,
       ROUND(100.0 * SUM(CASE WHEN on_scene >= 240 THEN 1 ELSE 0 END) / COUNT(*), 1)
                                                             AS pct_over_four_hours
FROM b
JOIN (SELECT CAST(year AS VARCHAR) AS year,
             COUNT(DISTINCT substr(incident_dt, 1, 7)) AS months
      FROM svfd.demo.fact_incident
      WHERE is_actual = 1 AND incident_dt IS NOT NULL
      GROUP BY 1) m
  ON m.year = b.year
GROUP BY 1
ORDER BY 1;
