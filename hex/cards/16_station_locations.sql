-- Card: Station locations
-- Geocoded from the department's own addresses (Nominatim, Nov 2026). Two are exact; the
-- Ketchum station is placed at the town reference point because the street address does not
-- geocode, and the card labels it approximate rather than implying a precision it lacks.
SELECT * FROM (VALUES
  ('ELK', 'Elkhorn station — Sun Valley',   43.6739206, -114.3236059, 'geocoded'),
  ('GH',  'Greenhorn station — Hailey',     43.5995090, -114.3479240, 'geocoded'),
  ('GB',  'Griffin Butte station — Ketchum', 43.6807000, -114.3637000, 'approximate')
) AS t(station_code, label, lat, lon, basis);
