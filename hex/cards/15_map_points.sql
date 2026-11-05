-- Card: Where calls happen
-- Definition: incidents placed by street-level geocoding, snapped to a ~150 m grid so the map
-- shows density instead of overplotting. Two honesty notes the card states on the page: these
-- are street centroids, not addresses, and the NERIS era has no address to geocode, so the
-- coverage by year is uneven.
SELECT ROUND(geo_lat, 3)                          AS lat,
       ROUND(geo_lon, 3)                          AS lon,
       COALESCE(NULLIF(incident_class, ''), 'Untyped') AS call_family,
       COUNT(*)                                   AS incidents
FROM svfd.demo.fact_incident
WHERE is_actual = 1
  AND geo_lat IS NOT NULL
  AND geo_lon IS NOT NULL
  AND CAST(substr(incident_dt, 1, 10) AS DATE) >= COALESCE(CAST({{start_date}} AS DATE), DATE '2020-01-01')
  AND CAST(substr(incident_dt, 1, 10) AS DATE) <= COALESCE(CAST({{end_date}} AS DATE), DATE '2099-12-31')
GROUP BY 1, 2, 3
ORDER BY incidents DESC;
