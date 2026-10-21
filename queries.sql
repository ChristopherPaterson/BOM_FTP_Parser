-- Example queries against the bom_db.py schema. They run on SQLite and PostgreSQL as written.
-- Times are epoch seconds (UTC). :now, :since, :until, :station, :aac and :state are parameters: use ? in
-- sqlite3, %s in psycopg, or put values in by hand to try them in a shell (sqlite3 bom.sqlite).
-- In SQLite, strftime('%s','now') gives "now"; in PostgreSQL, extract(epoch from now()).

-- 1. Latest reading at every station, windiest first.
SELECT s.name, s.state, o.t, o.wind_kn, o.gust_kn, o.dir, o.temp_c, o.pressure_hpa
FROM stations s
JOIN observations o
  ON o.station_id = s.id AND o.t = (SELECT MAX(t) FROM observations WHERE station_id = s.id)
WHERE o.t > :now - 3 * 3600
ORDER BY o.wind_kn DESC;

-- 2. One station's last 24 hours (find its id with query 3).
SELECT t, wind_kn, gust_kn, dir, temp_c, pressure_hpa
FROM observations
WHERE station_id = :station AND t > :now - 86400
ORDER BY t;

-- 3. Find a station by name.
SELECT id, name, state, lat, lon FROM stations WHERE LOWER(name) LIKE '%sydney%';

-- 4. Strongest gust per station over the last day.
SELECT s.name, MAX(o.gust_kn) AS max_gust_kn
FROM observations o JOIN stations s ON s.id = o.station_id
WHERE o.t > :now - 86400 AND o.gust_kn IS NOT NULL
GROUP BY s.name
ORDER BY max_gust_kn DESC
LIMIT 20;

-- 5. Hourly average wind at a station. SQLite: CAST(t / 3600 AS INTEGER); PostgreSQL: FLOOR(t / 3600).
SELECT CAST(t / 3600 AS INTEGER) * 3600 AS hour, ROUND(CAST(AVG(wind_kn) AS NUMERIC), 1) AS avg_kn, MAX(gust_kn) AS gust_kn
FROM observations
WHERE station_id = :station AND t > :now - 7 * 86400
GROUP BY 1 ORDER BY 1;

-- 6. Stations in a box (the nearest() function in bom_db.py adds distance sorting).
SELECT id, name, lat, lon FROM stations
WHERE lat BETWEEN -34.2 AND -33.5 AND lon BETWEEN 150.9 AND 151.5;

-- 7. Find a coastal waters zone by name.
SELECT aac, name, product, issued FROM zones WHERE LOWER(name) LIKE '%sydney%';

-- 8. The current forecast for a zone: its latest issue, periods not yet ended. texts is JSON
--    (forecast_winds, forecast_seas, forecast_swell1, forecast_weather, ...).
SELECT f.start_t, f.end_t, f.texts
FROM forecast_periods f JOIN zones z ON z.aac = f.aac AND z.issued = f.issued
WHERE f.aac = :aac AND f.end_t > :now
ORDER BY f.start_t;

-- 9. Just the wind text from that forecast.
--    SQLite:     json_extract(f.texts, '$.forecast_winds')
--    PostgreSQL: f.texts::json ->> 'forecast_winds'
SELECT f.start_t, json_extract(f.texts, '$.forecast_winds') AS winds
FROM forecast_periods f JOIN zones z ON z.aac = f.aac AND z.issued = f.issued
WHERE f.aac = :aac AND f.end_t > :now
ORDER BY f.start_t;

-- 10. What every issue said about one period: how the forecast changed as it got closer.
SELECT issued, texts FROM forecast_periods
WHERE aac = :aac AND start_t = :since
ORDER BY issued;

-- 11. The state synopsis for a zone.
SELECT z.name, s.text FROM zones z JOIN synopses s ON s.aac = z.parent WHERE z.aac = :aac;

-- 12. Warnings in force now, with the zones each one covers.
SELECT w.level, w.headline, w.effective, w.expires, z.aac
FROM warnings w JOIN warning_zones z ON z.id = w.id AND z.headline = w.headline
WHERE w.withdrawn IS NULL AND w.expires > :now
ORDER BY w.effective, z.aac;

-- 13. Is there a warning on a zone right now?
SELECT w.level, w.headline, w.expires
FROM warnings w JOIN warning_zones z ON z.id = w.id AND z.headline = w.headline
WHERE z.aac = :aac AND w.withdrawn IS NULL AND w.expires > :now;

-- 14. Warnings in force now for one state (zone codes start with the state: NSW_, VIC_, QLD_, SA_, TAS_, WA_, NT_).
SELECT DISTINCT w.level, w.headline, w.expires
FROM warnings w JOIN warning_zones z ON z.id = w.id AND z.headline = w.headline
WHERE z.aac LIKE 'NSW!_%' ESCAPE '!' AND w.withdrawn IS NULL AND w.expires > :now;

-- 15. Warning history: everything in force at some point in a period, withdrawn ones included.
SELECT level, headline, effective, expires, withdrawn
FROM warnings
WHERE COALESCE(effective, first_seen) <= :until AND COALESCE(withdrawn, expires) >= :since
ORDER BY effective;

-- 16. Gale-or-worse days per zone over the last 90 days.
SELECT z.aac, COUNT(DISTINCT CAST(w.effective / 86400 AS INTEGER)) AS days
FROM warnings w JOIN warning_zones z ON z.id = w.id AND z.headline = w.headline
WHERE w.level IN ('gale', 'storm', 'hurricane') AND w.effective > :now - 90 * 86400
GROUP BY z.aac ORDER BY days DESC;

-- 17. Keep the database small (bom_db.prune() does this): drop readings older than 30 days.
DELETE FROM observations WHERE t < :now - 30 * 86400;
