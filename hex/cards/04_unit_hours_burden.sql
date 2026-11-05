-- Card: Burden - hours out of scope (workload)
-- Definition: crew time consumed, cleared minus dispatch, summed per apparatus and grouped by
-- year. The two series are the same baskets the response charts use: in scope, and burden
-- (mutual aid given, or a wildland deployment). This is the workload measure; a count of calls
-- says nothing about whether they ate one hour or six.
-- The join pairs a unit row with the incident from its own dispatch year, because incident
-- numbers repeat across years and a bare join would duplicate rows.
-- A year is plotted only when at least 90% of its incidents carry apparatus records, because
-- 2026 sits near 5% and its bars would read as a workload collapse rather than missing data.
WITH i AS (
  SELECT incident_key, year, mutual_aid_given, incident_class
  FROM svfd.demo.fact_incident
),
u AS (
  SELECT CAST(substr(dispatch_dt, 1, 4) AS INT) AS dispatch_year,
         unit_hours, incident_key
  FROM svfd.demo.fact_unit_response
  WHERE dispatch_dt IS NOT NULL
    AND CAST(substr(dispatch_dt, 1, 10) AS DATE) >= COALESCE(CAST({{start_date}} AS DATE), DATE '2020-01-01')
    AND CAST(substr(dispatch_dt, 1, 10) AS DATE) <= COALESCE(CAST({{end_date}} AS DATE), DATE '2099-12-31')
    AND unit_hours IS NOT NULL
),
coverage AS (
  SELECT CAST(fi.year AS VARCHAR) AS yr,
         ROUND(100.0 * COUNT(ur.incident_key) / COUNT(*), 1) AS pct_with_unit_rows
  FROM svfd.demo.fact_incident fi
  LEFT JOIN (SELECT DISTINCT incident_key FROM svfd.demo.fact_unit_response) ur
         ON ur.incident_key = fi.incident_key
  WHERE fi.is_actual = 1
    AND fi.year IS NOT NULL
  GROUP BY 1
),
plottable AS (
  SELECT yr FROM coverage WHERE pct_with_unit_rows >= 90
),
months AS (
  SELECT year, COUNT(DISTINCT substr(incident_dt, 1, 7)) AS n_months
  FROM svfd.demo.fact_incident
  WHERE is_actual = 1 AND incident_dt IS NOT NULL
  GROUP BY 1
)
SELECT CASE WHEN m.n_months < 12
            THEN CAST(u.dispatch_year AS VARCHAR) || ' (' || m.n_months || ' mo)'
            ELSE CAST(u.dispatch_year AS VARCHAR) END                    AS year,
       ROUND(SUM(CASE WHEN COALESCE(i.mutual_aid_given, '') = ''
                       AND COALESCE(i.incident_class, '') <> 'Wildland'
                      THEN u.unit_hours ELSE 0 END), 1)                 AS "in scope hours",
       ROUND(SUM(CASE WHEN COALESCE(i.mutual_aid_given, '') = ''
                       AND COALESCE(i.incident_class, '') <> 'Wildland'
                      THEN 0 ELSE u.unit_hours END), 1)                 AS "burden hours",
       COUNT(*)                                                        AS unit_rows
FROM u
LEFT JOIN i
       ON i.incident_key = u.incident_key
      AND i.year         = u.dispatch_year
JOIN plottable p ON p.yr = CAST(u.dispatch_year AS VARCHAR)
JOIN months m    ON m.year = u.dispatch_year
GROUP BY u.dispatch_year, m.n_months
ORDER BY u.dispatch_year;
