"""A small web app on the standard library alone: collects BOM every five minutes into SQLite in a
background thread, serves a JSON API, and serves a map page (examples/map.html) at /.

    python examples/web_api.py bom.sqlite                       # live from BOM
    python examples/web_api.py bom.sqlite --sample samples/bom.json  # offline, from the sample
    open http://127.0.0.1:8000/

Endpoints (all GET, JSON):
    /api/stations?state=NSW              latest reading per station
    /api/near?lat=-33.85&lon=151.27&km=20 stations near a point, nearest first
    /api/station/066214?hours=24         one station's readings
    /api/zones?q=sydney                  find zones
    /api/forecast/NSW_MW009              a zone's current forecast
    /api/warnings?state=TAS | ?zone=...  warnings in force
"""

import argparse
import json
import logging
import pathlib
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import bom_db  # noqa: E402
import bom_ftp  # noqa: E402

log = logging.getLogger("web_api")
STATES = set(bom_ftp.STATES.values())
CODE = re.compile(r"^[A-Za-z0-9_]{1,20}$")
META = {"attribution": bom_ftp.ATTRIBUTION, "disclaimer": bom_ftp.DISCLAIMER}


def collect(db: str):
    """Background collector: one connection of its own (sqlite connections stay on one thread)."""
    conn, feed = bom_db.connect(db), bom_ftp.Feed()
    while True:
        try:
            bom_db.save(conn, bom_ftp.fetch_all(feed))
            bom_db.prune(conn, days=30)
            log.info("collected")
        except Exception:
            log.exception("collect failed")  # keep serving what is already stored
        time.sleep(300)


class BadRequest(Exception):
    pass


def num(q, key, lo, hi, default=None):
    v = q.get(key, [default])[0]
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise BadRequest(f"{key} must be a number")
    if not lo <= x <= hi:
        raise BadRequest(f"{key} out of range")
    return x


def route(conn, path: str, q: dict, now: float):
    """(status, body) for one request. Keep routing here so any framework can reuse it."""
    parts = [p for p in path.split("/") if p]
    if parts[:1] != ["api"] or len(parts) > 3:
        return 404, {"error": "not found"}
    what, arg = (parts + [None, None])[1:3]
    if arg is not None and not CODE.match(arg):
        raise BadRequest("bad code")
    state = q.get("state", [None])[0]
    if state is not None and state.upper() not in STATES:
        raise BadRequest(f"state must be one of {sorted(STATES)}")
    state = state.upper() if state else None

    if what == "stations":
        return 200, bom_db.latest(conn, state=state, now=now)
    if what == "near":
        lat, lon = num(q, "lat", -90, 90), num(q, "lon", -180, 180)
        return 200, bom_db.nearest(conn, lat, lon, km=num(q, "km", 0, 500, 25))
    if what == "station" and arg:
        hours = num(q, "hours", 0, 24 * 30, 24)
        return 200, bom_db.history(conn, arg, since=now - hours * 3600, until=now)
    if what == "zones":
        return 200, bom_db.search_zones(conn, q.get("q", [""])[0][:50])
    if what == "forecast" and arg:
        f = bom_db.forecast(conn, arg.upper(), now=now)
        return (200, f) if f else (404, {"error": "no such zone"})
    if what == "warnings":
        zone = q.get("zone", [None])[0]
        if zone and not CODE.match(zone):
            raise BadRequest("bad zone")
        return 200, bom_db.active_warnings(conn, aac=zone and zone.upper(), state=state, now=now)
    return 404, {"error": "not found"}


def handler(db: str, now_fn):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            u = urlparse(self.path)
            if u.path in ("/", "/index.html"):
                return self.send(200, (HERE / "map.html").read_bytes(), "text/html; charset=utf-8")
            conn = bom_db.connect(db)
            try:
                status, body = route(conn, u.path, parse_qs(u.query), now_fn())
            except BadRequest as e:
                status, body = 400, {"error": str(e)}
            finally:
                conn.close()
            out = {**META, "data": body} if status == 200 else body
            self.send(status, json.dumps(out).encode(), "application/json")

        def send(self, status, body, ctype):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            log.debug(fmt, *args)

    return H


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to serve your network")
    ap.add_argument("--sample", help="load a saved bom_ftp output and serve it, no collecting")
    a = ap.parse_args()

    now_fn = time.time
    if a.sample:
        data = json.loads(pathlib.Path(a.sample).read_text(encoding="utf-8"))
        bom_db.save(bom_db.connect(a.db), data)
        now_fn = lambda: data["fetched"]  # noqa: E731  the sample's own time, so nothing looks stale
    else:
        threading.Thread(target=collect, args=(a.db,), daemon=True).start()
    log.info("http://%s:%d/", a.host, a.port)
    ThreadingHTTPServer((a.host, a.port), handler(a.db, now_fn)).serve_forever()


if __name__ == "__main__":
    main()
