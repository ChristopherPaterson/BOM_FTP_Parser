"""Bureau of Meteorology (Australia) anonymous FTP feeds: live station readings, coastal waters forecasts
and marine warnings, parsed into plain dicts. Standard library only, one file: copy it into your app.

Everything comes from ftp://ftp.bom.gov.au/anon/gen/fwo/, BOM's documented machine-readable products:

- Station readings: ID?60920.xml per state, the latest reading at every station (wind, gust, temperature,
  pressure).
- Coastal waters forecasts: each state's bundle plus the local waters products (Sydney Enclosed Waters,
  Port Phillip and Western Port, the Derwent, Adelaide and Perth), BOM's own text per zone and period.
- Warnings: every CAP warning (*.cap.xml) that names a coastal waters zone (AMOC area codes like
  NSW_MW004): strong wind, gale, storm force and hurricane force wind warnings, hazardous surf and the like.

Storage, if you want it, is in bom_db.py.

    feed = Feed()
    data = fetch_all(feed)   # call again every few minutes with the same Feed: unchanged files are not
                             # downloaded again

Command line: python bom_ftp.py > bom.json
"""

import io
import json
import logging
import math
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from ftplib import FTP
from functools import partial

log = logging.getLogger("bom_ftp")

HOST, DIR = "ftp.bom.gov.au", "/anon/gen/fwo"
STATES = {"N": "NSW", "V": "VIC", "Q": "QLD", "S": "SA", "T": "TAS", "W": "WA", "D": "NT"}
OBSERVATIONS = [f"ID{k}60920.xml" for k in STATES]
# State coastal waters bundles, then local waters (bays, harbours, estuaries).
FORECASTS = [
    "IDN11001.xml",
    "IDV10200.xml",
    "IDQ11290.xml",
    "IDS11072.xml",
    "IDT12329.xml",
    "IDW11160.xml",
    "IDD11030.xml",
    "IDN11013.xml",
    "IDV10465.xml",
    "IDT12460.xml",
    "IDS10035.xml",
    "IDW11500.xml",
]
WARNINGS = re.compile(r"^ID[A-Z]\d{5}\.cap\.xml$")
MAX_BYTES = 3_000_000
AREA = re.compile(r"^[A-Z]{2,3}_MW\d{3}$")
LEVELS = [("hurricane", "hurricane"), ("storm force", "storm"), ("gale", "gale"), ("strong wind", "strong")]
LINKS = {
    "coastal": "https://www.bom.gov.au/coastal-location/australia",
    "warnings": "https://www.bom.gov.au/weather-and-climate/warnings-and-alerts",
}
ATTRIBUTION = "© Commonwealth of Australia, Bureau of Meteorology"
DISCLAIMER = "Not for navigation. Check www.bom.gov.au for the latest official forecasts and warnings."


# ---- fetching ----


class Feed:
    """Anonymous FTP with a per-file cache: a file is downloaded again only when its server time changes."""

    def __init__(self):
        self.cache: dict[str, tuple[str, bytes]] = {}

    def fetch(self, names_wanted=None, pattern=None) -> dict[str, bytes]:
        out = {}
        with FTP(HOST, timeout=60) as f:
            f.login()
            f.cwd(DIR)
            names = list(names_wanted or [])
            if pattern:
                names += [n for n in f.nlst() if pattern.match(n)]
            for n in names:
                try:
                    stamp = f.voidcmd(f"MDTM {n}")
                except Exception:
                    continue  # not published right now (warnings come and go)
                if n in self.cache and self.cache[n][0] == stamp:
                    out[n] = self.cache[n][1]
                    continue
                buf = io.BytesIO()
                f.retrbinary(f"RETR {n}", partial(_capped, buf))
                body = buf.getvalue()
                if len(body) >= MAX_BYTES:
                    log.warning("%s too large, skipped", n)
                    continue
                self.cache[n] = (stamp, body)
                out[n] = body
        for gone in set(self.cache) - set(out):
            if pattern and pattern.match(gone):
                del self.cache[gone]  # a warning that has been withdrawn
        return out


def _capped(buf: io.BytesIO, chunk: bytes):
    """Keep reading a file but stop storing it past MAX_BYTES (the caller then skips it)."""
    if buf.tell() < MAX_BYTES:
        buf.write(chunk)


def _xml(raw: bytes):
    head = raw[:4096].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in raw.upper():
        raise ValueError("DTDs and entities are not accepted")
    return ET.fromstring(raw)


def _num(v, lo, hi):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and lo <= x <= hi else None


def _utc(s):
    if not s:
        return None
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d.timestamp() if d.tzinfo else None


def _text(node):
    return " ".join("".join(node.itertext()).split()) if node is not None else ""


# ---- parsing ----


def parse_observations(raw: bytes, letter: str) -> list[dict]:
    """Stations from one ID?60920.xml; `letter` is the state letter after "ID" (N, V, Q, S, T, W, D)."""
    root = _xml(raw)
    out = []
    for s in root.iter("station"):
        lat, lon = _num(s.get("lat"), -90, 90), _num(s.get("lon"), -180, 180)
        p = s.find("period")
        if lat is None or lon is None or p is None or not s.get("bom-id"):
            continue
        t = _utc(p.get("time-utc"))
        if t is None:
            continue
        el = {e.get("type"): e for e in p.iter("element")}

        def val(key, units, lo, hi, el=el):
            e = el.get(key)
            return _num(e.text, lo, hi) if e is not None and e.get("units") == units else None

        wmo = s.get("wmo-id")
        out.append(
            {
                "id": s.get("bom-id"),
                "name": s.get("description") or s.get("stn-name"),
                "state": STATES[letter],
                "lat": lat,
                "lon": lon,
                "t": t,
                "wind_kn": val("wind_spd", "knots", 0, 250),
                "gust_kn": val("wind_gust_spd", "knots", 0, 300),
                "dir": val("wind_dir_deg", "deg", 0, 360),
                "temp_c": val("air_temperature", "Celsius", -60, 60),
                "pressure_hpa": val("msl_pres", "hPa", 850, 1100),
                "height_m": _num(s.get("stn-height"), -500, 9000),
                "url": f"https://www.bom.gov.au/products/ID{letter}60901/ID{letter}60901.{wmo}.shtml"
                if wmo
                else None,
            }
        )
    return out


def parse_forecast(raw: bytes) -> tuple[dict, dict]:
    """Zones {aac: {...}} and synopses {aac: text} from one coastal waters product. A synopsis is keyed by the
    state region (a zone's parent) in the state bundles, and by the zone itself in local waters products."""
    root = _xml(raw)
    product = root.findtext("amoc/identifier")
    issued = _utc(root.findtext("amoc/issue-time-utc"))
    zones, synopsis = {}, {}
    for a in root.iter("area"):
        aac = a.get("aac", "")
        if a.get("type") in ("region", "sub-region"):
            for t in a.iter("text"):
                if t.get("type") == "synoptic_situation":
                    synopsis[aac] = _text(t)
            continue
        if not AREA.match(aac):
            continue
        periods = []
        for fp in a.findall("forecast-period"):
            texts = {t.get("type"): _text(t) for t in fp.findall("text") if _text(t)}
            # Local waters products carry the zone's own synopsis as a period spanning the whole forecast.
            if "synoptic_situation" in texts:
                synopsis[aac] = texts.pop("synoptic_situation")
            if texts:
                periods.append(
                    {
                        "start": _utc(fp.get("start-time-utc")),
                        "end": _utc(fp.get("end-time-utc")),
                        "text": texts,
                    }
                )
        if periods:
            periods.sort(key=lambda p: p["start"] or 0)
            zones[aac] = {
                "name": a.get("description"),
                "product": product,
                "issued": issued,
                "periods": periods,
                "parent": a.get("parent-aac"),
            }
    return zones, synopsis


def parse_warning(raw: bytes, now: float) -> list[dict]:
    """Current, non-cancelled marine parts of one CAP warning."""
    ns = "{urn:oasis:names:tc:emergency:cap:1.2}"
    root = _xml(raw)
    if root.tag != ns + "alert" or root.findtext(ns + "status") != "Actual":
        return []
    out = []
    for info in root.findall(ns + "info"):
        headline = _text(info.find(ns + "headline"))
        expires = _utc(info.findtext(ns + "expires"))
        effective = _utc(info.findtext(ns + "effective"))
        if not headline or expires is None or expires < now or headline.lower().startswith("cancellation"):
            continue
        zones = [
            g.findtext(ns + "value")
            for area in info.findall(ns + "area")
            for g in area.findall(ns + "geocode")
            if g.findtext(ns + "valueName") == "AMOC-AreaCode" and AREA.match(g.findtext(ns + "value") or "")
        ]
        if not zones:
            continue
        low = headline.lower()
        level = next((lv for word, lv in LEVELS if word in low), "other")
        out.append(
            {
                "id": root.findtext(ns + "identifier"),
                "headline": headline,
                "event": _text(info.find(ns + "event")),
                "level": level,
                "severity": _text(info.find(ns + "severity")),
                "effective": effective,
                "expires": expires,
                "zones": zones,
                "url": LINKS["warnings"],
            }
        )
    return out


# ---- everything at once ----


def parse_all(files: dict[str, bytes], now: float | None = None) -> dict:
    """Stations, zones, synopses and warnings from fetched files {name: bytes}. A file that fails to parse
    is logged and skipped; the rest still come through."""
    now = now or time.time()
    stations, zones, synopsis, warnings = [], {}, {}, []
    for name in OBSERVATIONS:
        if name in files:
            try:
                stations += parse_observations(files[name], name[2])
            except Exception as e:
                log.warning("%s: %s", name, e)
    for name in FORECASTS:
        if name in files:
            try:
                z, syn = parse_forecast(files[name])
                for aac, v in z.items():  # a zone in both a bundle and a local product: the later issue wins
                    if aac not in zones or (v["issued"] or 0) >= (zones[aac]["issued"] or 0):
                        zones[aac] = v
                synopsis.update(syn)
            except Exception as e:
                log.warning("%s: %s", name, e)
    for name, raw in files.items():
        if name.endswith(".cap.xml"):
            try:
                warnings += parse_warning(raw, now)
            except Exception as e:
                log.warning("%s: %s", name, e)
    return {
        "fetched": now,
        "attribution": ATTRIBUTION,
        "links": LINKS,
        "disclaimer": DISCLAIMER,
        "stations": stations,
        "zones": zones,
        "synopsis": synopsis,
        "warnings": warnings,
    }


def fetch_all(feed: Feed | None = None, now: float | None = None) -> dict:
    """One pass over every product. Keep the Feed between calls so unchanged files are not downloaded again."""
    files = (feed or Feed()).fetch(OBSERVATIONS + FORECASTS, WARNINGS)
    return parse_all(files, now)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    json.dump(fetch_all(), sys.stdout, indent=1, ensure_ascii=False)
