"""Conditions at one spot, straight from BOM, no database: the nearest stations, the zone forecast and any
warnings on the zone. The simplest way to use bom_ftp.py.

    python examples/my_spot.py --lat -33.85 --lon 151.27 --zone NSW_MW009     # Sydney Harbour
    python examples/my_spot.py --lat -33.85 --lon 151.27 --zone NSW_MW009 --sample samples/bom.json

Find your zone code with: python examples/my_spot.py --find sydney
"""

import argparse
import json
import math
import pathlib
import sys
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import bom_ftp  # noqa: E402

COMPASS = "N NNE NE ENE E ESE SE SSE S SSW SW WSW W WNW NW NNW".split()


def km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 12742 * math.asin(math.sqrt(a))


def local(t):
    return datetime.fromtimestamp(t).strftime("%a %H:%M") if t else "?"


def wind(s):
    if s["wind_kn"] is None:
        return "no wind reading"
    d = COMPASS[round(s["dir"] / 22.5) % 16] if s["dir"] is not None else "variable"
    gust = f", gusting {s['gust_kn']:.0f}" if s["gust_kn"] else ""
    return f"{d} {s['wind_kn']:.0f} kn{gust}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--zone", help="coastal waters zone code, e.g. NSW_MW009")
    ap.add_argument("--find", help="list zones whose name contains this")
    ap.add_argument("--sample", help="read a saved bom_ftp output instead of BOM's FTP")
    a = ap.parse_args()

    data = json.loads(pathlib.Path(a.sample).read_text(encoding="utf-8")) if a.sample else bom_ftp.fetch_all()

    if a.find:
        for aac, z in sorted(data["zones"].items()):
            if a.find.lower() in z["name"].lower():
                print(aac, z["name"])
        return

    if a.lat is not None and a.lon is not None:
        near = sorted(data["stations"], key=lambda s: km(a.lat, a.lon, s["lat"], s["lon"]))[:3]
        print("Nearest stations")
        for s in near:
            print(f"  {s['name']} ({km(a.lat, a.lon, s['lat'], s['lon']):.1f} km): {wind(s)} at {local(s['t'])}")

    if a.zone:
        z = data["zones"].get(a.zone)
        if z:
            print(f"\n{z['name']} forecast, issued {local(z['issued'])}")
            for p in z["periods"][:3]:
                print(f"  {local(p['start'])}: {p['text'].get('forecast_winds', '')} "
                      f"{p['text'].get('forecast_seas', '')}".rstrip())
            syn = data["synopsis"].get(a.zone) or data["synopsis"].get(z["parent"])
            if syn:
                print(f"  Synopsis: {syn}")
        else:
            print(f"\nNo forecast for {a.zone} (try --find)")
        hits = [w for w in data["warnings"] if a.zone in w["zones"]]
        print("\nWarnings: " + ("; ".join(w["headline"] for w in hits) if hits else "none"))

    print(f"\n{data['attribution']}. {data['disclaimer']}")


if __name__ == "__main__":
    main()
