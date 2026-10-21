"""Alerts: tell me when a new warning covers my zones, or a station I care about gets windy. Collects into
SQLite itself, so it runs on its own (cron, systemd, a container) or beside another app.

    python examples/alerts.py bom.sqlite --zone NSW_MW004 --zone NSW_MW009 --station 066196 --over 25

Alerts print to stdout. Set BOM_ALERT_WEBHOOK to also POST each one as JSON {"text": ...} (Slack, Discord,
ntfy, Home Assistant and most chat tools accept that shape). Keep the URL in the environment, not in code.
"""

import argparse
import json
import logging
import os
import pathlib
import sys
import time
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import bom_db  # noqa: E402
import bom_ftp  # noqa: E402

log = logging.getLogger("alerts")


def notify(text: str):
    print(text, flush=True)
    url = os.environ.get("BOM_ALERT_WEBHOOK")
    if url:
        req = urllib.request.Request(url, json.dumps({"text": text}).encode(), {"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=10).close()
        except Exception as e:
            log.warning("webhook failed: %s", e)


def check(conn, zones, stations, over, sent: set, now: float):
    """Send each alert once: a warning once per (id, headline), a station once until it drops below `over`."""
    for aac in zones:
        for w in bom_db.active_warnings(conn, aac=aac, now=now):
            key = ("w", w["id"], w["headline"])
            if key not in sent:
                sent.add(key)
                notify(f"{w['headline']} (until {time.strftime('%a %H:%M', time.localtime(w['expires']))})")
    latest = {r["id"]: r for r in bom_db.latest(conn, now=now)}
    for sid in stations:
        r = latest.get(sid)
        key = ("s", sid)
        if r and r["wind_kn"] is not None and r["wind_kn"] >= over:
            if key not in sent:
                sent.add(key)
                gust = f", gust {r['gust_kn']:.0f} kn" if r["gust_kn"] else ""
                notify(f"{r['name']}: {r['wind_kn']:.0f} kn{gust}")
        else:
            sent.discard(key)  # dropped back below: alert again next time it rises


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--zone", action="append", default=[], help="zone code, repeatable")
    ap.add_argument("--station", action="append", default=[], help="station id, repeatable")
    ap.add_argument("--over", type=float, default=25, help="station wind alert threshold, knots")
    ap.add_argument("--once", action="store_true", help="one pass then exit (for cron)")
    a = ap.parse_args()

    # ponytail: `sent` lives in memory, so a restart re-sends what is in force; store it in the DB if that matters.
    conn, feed, sent = bom_db.connect(a.db), bom_ftp.Feed(), set()
    while True:
        try:
            bom_db.save(conn, bom_ftp.fetch_all(feed))
            check(conn, a.zone, a.station, a.over, sent, time.time())
        except Exception:
            log.exception("pass failed")
        if a.once:
            return
        time.sleep(300)


if __name__ == "__main__":
    main()
