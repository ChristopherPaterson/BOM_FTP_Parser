"""Store bom_ftp output in a database and query it. Standard library only (sqlite3). The schema is plain SQL
that also runs on PostgreSQL; see queries.sql for the same queries as raw SQL.

    import bom_ftp, bom_db
    conn = bom_db.connect("bom.sqlite")
    bom_db.save(conn, bom_ftp.fetch_all())
    bom_db.latest(conn, state="NSW")

What is kept:
- stations: one row per station, details refreshed each pass.
- observations: every reading, so history builds up (prune() trims it).
- zones and forecast_periods: every forecast issue, so you can see what was forecast at any time.
- synopses: the latest synopsis per state region.
- warnings and warning_zones: every warning seen, with first_seen, last_seen and, when it leaves the
  feed before it expires, withdrawn.

Command line:
    python bom_db.py bom.sqlite                  # one pass from BOM's FTP
    python bom_db.py bom.sqlite --loop           # every five minutes, keeping 30 days
    python bom_db.py bom.sqlite --load bom.json  # from a saved bom_ftp.py output (e.g. samples/bom.json)
"""

import json
import logging
import math
import sqlite3
import sys
import time

log = logging.getLogger("bom_db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS stations (
    id TEXT PRIMARY KEY, name TEXT, state TEXT, lat DOUBLE PRECISION, lon DOUBLE PRECISION,
    height_m DOUBLE PRECISION, url TEXT, last_seen DOUBLE PRECISION
);
CREATE TABLE IF NOT EXISTS observations (
    station_id TEXT NOT NULL, t DOUBLE PRECISION NOT NULL, wind_kn DOUBLE PRECISION,
    gust_kn DOUBLE PRECISION, dir DOUBLE PRECISION, temp_c DOUBLE PRECISION, pressure_hpa DOUBLE PRECISION,
    PRIMARY KEY (station_id, t)
);
CREATE INDEX IF NOT EXISTS observations_t ON observations (t);
CREATE TABLE IF NOT EXISTS zones (
    aac TEXT PRIMARY KEY, name TEXT, parent TEXT, product TEXT, issued DOUBLE PRECISION
);
CREATE TABLE IF NOT EXISTS forecast_periods (
    aac TEXT NOT NULL, issued DOUBLE PRECISION NOT NULL, start_t DOUBLE PRECISION NOT NULL,
    end_t DOUBLE PRECISION, product TEXT, texts TEXT,
    PRIMARY KEY (aac, issued, start_t)
);
CREATE TABLE IF NOT EXISTS synopses (aac TEXT PRIMARY KEY, text TEXT, fetched DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS warnings (
    id TEXT NOT NULL, headline TEXT NOT NULL, event TEXT, level TEXT, severity TEXT,
    effective DOUBLE PRECISION, expires DOUBLE PRECISION, url TEXT,
    first_seen DOUBLE PRECISION, last_seen DOUBLE PRECISION, withdrawn DOUBLE PRECISION,
    PRIMARY KEY (id, headline)
);
CREATE TABLE IF NOT EXISTS warning_zones (
    id TEXT NOT NULL, headline TEXT NOT NULL, aac TEXT NOT NULL, PRIMARY KEY (id, headline, aac)
);
CREATE INDEX IF NOT EXISTS warning_zones_aac ON warning_zones (aac);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # readers keep working while a pass is being saved
    conn.executescript(SCHEMA)
    return conn


def save(conn: sqlite3.Connection, data: dict):
    """Store one bom_ftp.parse_all / fetch_all result. Safe to repeat: rows already stored are not doubled."""
    now = data["fetched"]
    with conn:
        for s in data["stations"]:
            conn.execute(
                "INSERT INTO stations VALUES (?,?,?,?,?,?,?,?) ON CONFLICT (id) DO UPDATE SET name=excluded.name,"
                " state=excluded.state, lat=excluded.lat, lon=excluded.lon, height_m=excluded.height_m,"
                " url=excluded.url, last_seen=excluded.last_seen",
                (s["id"], s["name"], s["state"], s["lat"], s["lon"], s["height_m"], s["url"], now),
            )
            conn.execute(
                "INSERT INTO observations VALUES (?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                (s["id"], s["t"], s["wind_kn"], s["gust_kn"], s["dir"], s["temp_c"], s["pressure_hpa"]),
            )
        for aac, z in data["zones"].items():
            conn.execute(
                "INSERT INTO zones VALUES (?,?,?,?,?) ON CONFLICT (aac) DO UPDATE SET name=excluded.name,"
                " parent=excluded.parent, product=excluded.product, issued=excluded.issued",
                (aac, z["name"], z["parent"], z["product"], z["issued"]),
            )
            for p in z["periods"]:
                if z["issued"] is None or p["start"] is None:
                    continue  # part of the key; a malformed period must not abort the whole save
                conn.execute(
                    "INSERT INTO forecast_periods VALUES (?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                    (aac, z["issued"], p["start"], p["end"], z["product"], json.dumps(p["text"])),
                )
        for aac, text in data["synopsis"].items():
            conn.execute(
                "INSERT INTO synopses VALUES (?,?,?) ON CONFLICT (aac) DO UPDATE SET text=excluded.text,"
                " fetched=excluded.fetched",
                (aac, text, now),
            )
        seen = set()
        for w in data["warnings"]:
            key = (w["id"], w["headline"])
            seen.add(key)
            conn.execute(
                "INSERT INTO warnings VALUES (?,?,?,?,?,?,?,?,?,?,NULL) ON CONFLICT (id, headline) DO UPDATE"
                " SET last_seen=excluded.last_seen, expires=excluded.expires, withdrawn=NULL",
                (*key, w["event"], w["level"], w["severity"], w["effective"], w["expires"], w["url"], now, now),
            )
            for aac in w["zones"]:
                conn.execute("INSERT INTO warning_zones VALUES (?,?,?) ON CONFLICT DO NOTHING", (*key, aac))
        # Gone from the feed before it expired: BOM withdrew or cancelled it.
        # ponytail: a warning file that fails to parse in one pass also counts as withdrawn until the next pass.
        for r in conn.execute("SELECT id, headline FROM warnings WHERE withdrawn IS NULL AND expires > ?", (now,)):
            if (r[0], r[1]) not in seen:
                conn.execute("UPDATE warnings SET withdrawn=? WHERE id=? AND headline=?", (now, r[0], r[1]))


def prune(conn: sqlite3.Connection, days: float = 30, now: float | None = None):
    """Drop readings, forecast issues and finished warnings older than `days`."""
    cut = (now or time.time()) - days * 86400
    with conn:
        conn.execute("DELETE FROM observations WHERE t < ?", (cut,))
        conn.execute("DELETE FROM forecast_periods WHERE end_t < ?", (cut,))
        conn.execute("DELETE FROM warning_zones WHERE (id, headline) IN"
                     " (SELECT id, headline FROM warnings WHERE expires < ?)", (cut,))
        conn.execute("DELETE FROM warnings WHERE expires < ?", (cut,))


# ---- queries ----


def _rows(cur) -> list[dict]:
    return [dict(r) for r in cur]


def latest(conn, state: str | None = None, max_age_s: float = 3 * 3600, now: float | None = None) -> list[dict]:
    """Each station's latest reading (within max_age_s), optionally for one state, windiest first."""
    cut = (now or time.time()) - max_age_s
    return _rows(conn.execute(
        # One primary-key lookup per station: fast however much history is kept.
        "SELECT s.*, o.t, o.wind_kn, o.gust_kn, o.dir, o.temp_c, o.pressure_hpa FROM stations s"
        " JOIN observations o ON o.station_id = s.id"
        "  AND o.t = (SELECT MAX(t) FROM observations WHERE station_id = s.id)"
        " WHERE o.t >= ? AND (? IS NULL OR s.state = ?)"
        " ORDER BY o.wind_kn IS NULL, o.wind_kn DESC",
        (cut, state, state),
    ))


def history(conn, station_id: str, since: float, until: float | None = None) -> list[dict]:
    """One station's readings between two times, oldest first."""
    return _rows(conn.execute(
        "SELECT * FROM observations WHERE station_id = ? AND t >= ? AND t <= ? ORDER BY t",
        (station_id, since, until or time.time()),
    ))


def find_stations(conn, name: str) -> list[dict]:
    """Stations whose name contains `name` (case-insensitive)."""
    return _rows(conn.execute(
        "SELECT * FROM stations WHERE LOWER(name) LIKE ? ORDER BY name", (f"%{name.lower()}%",)
    ))


def nearest(conn, lat: float, lon: float, km: float = 50, limit: int = 10) -> list[dict]:
    """Stations within `km` of a point, nearest first, each with its distance."""
    dlat = km / 111.0
    dlon = km / (111.0 * max(math.cos(math.radians(lat)), 0.01))
    rows = _rows(conn.execute(
        "SELECT * FROM stations WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
        (lat - dlat, lat + dlat, lon - dlon, lon + dlon),
    ))
    for r in rows:
        r["km"] = round(_km(lat, lon, r["lat"], r["lon"]), 1)
    return sorted((r for r in rows if r["km"] <= km), key=lambda r: r["km"])[:limit]


def _km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 12742 * math.asin(math.sqrt(a))


def forecast(conn, aac: str, now: float | None = None) -> dict | None:
    """A zone's latest forecast: the zone, its periods not yet ended, and its state synopsis."""
    z = conn.execute("SELECT * FROM zones WHERE aac = ?", (aac,)).fetchone()
    if z is None:
        return None
    periods = _rows(conn.execute(
        "SELECT start_t, end_t, texts FROM forecast_periods WHERE aac = ? AND issued = ? AND end_t > ?"
        " ORDER BY start_t",
        (aac, z["issued"], now or time.time()),
    ))
    for p in periods:
        p["texts"] = json.loads(p["texts"])
    # The zone's own synopsis (local waters) if it has one, else its state region's.
    syn = conn.execute("SELECT text FROM synopses WHERE aac IN (?, ?) ORDER BY aac = ? DESC",
                       (aac, z["parent"], aac)).fetchone()
    return {**dict(z), "periods": periods, "synopsis": syn[0] if syn else None}


def search_zones(conn, name: str) -> list[dict]:
    """Zones whose name contains `name`, e.g. "sydney"."""
    return _rows(conn.execute(
        "SELECT * FROM zones WHERE LOWER(name) LIKE ? ORDER BY aac", (f"%{name.lower()}%",)
    ))


def active_warnings(conn, aac: str | None = None, state: str | None = None, now: float | None = None) -> list[dict]:
    """Warnings in force now (not expired, not withdrawn), optionally for one zone or one state
    (state = the zone code prefix: NSW, VIC, QLD, SA, TAS, WA, NT). Each comes with its zones."""
    now = now or time.time()
    rows = _rows(conn.execute(
        "SELECT DISTINCT w.* FROM warnings w JOIN warning_zones z ON z.id = w.id AND z.headline = w.headline"
        " WHERE w.withdrawn IS NULL AND w.expires > ? AND (? IS NULL OR z.aac = ?) AND z.aac LIKE ? ESCAPE '!'"
        " ORDER BY w.effective",
        (now, aac, aac, f"{state.upper()}!_%" if state else "%"),
    ))
    for w in rows:
        w["zones"] = [r[0] for r in conn.execute(
            "SELECT aac FROM warning_zones WHERE id = ? AND headline = ? ORDER BY aac", (w["id"], w["headline"])
        )]
    return rows


def warnings_between(conn, since: float, until: float | None = None) -> list[dict]:
    """Every warning in force at some point between two times (history, including withdrawn ones)."""
    return _rows(conn.execute(
        "SELECT * FROM warnings WHERE COALESCE(effective, first_seen) <= ? AND COALESCE(withdrawn, expires) >= ?"
        " ORDER BY effective",
        (until or time.time(), since),
    ))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    conn = connect(args[0])
    if "--load" in args:
        with open(args[args.index("--load") + 1], encoding="utf-8") as f:
            save(conn, json.load(f))
        sys.exit()
    import bom_ftp

    feed = bom_ftp.Feed()
    while True:
        try:
            save(conn, bom_ftp.fetch_all(feed))
            prune(conn, days=30)
            log.info("saved")
        except Exception:
            log.exception("pass failed")
        if "--loop" not in args:
            break
        time.sleep(300)
