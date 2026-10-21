"""BOM's coastal waters zones (spatial dataset IDM00003, ftp://ftp.bom.gov.au/anon/home/adfd/spatial/) as
simplified GeoJSON. Run when BOM changes the zones:

    uv run --with pyshp --with shapely python tools/build_zones.py IDM00003.shp bom_zones.geojson
"""

import json
import sys

import shapefile
from shapely.geometry import mapping, shape

src, out = sys.argv[1:3]
features = []
for rec in shapefile.Reader(src, encoding="latin1").shapeRecords():
    r = rec.record.as_dict()
    if "_MW" not in r["AAC"]:
        continue
    geom = shape(rec.shape.__geo_interface__).simplify(0.005, preserve_topology=True)
    g = json.loads(json.dumps(mapping(geom)), parse_float=lambda x: round(float(x), 4))
    features.append(
        {
            "type": "Feature",
            "properties": {"aac": r["AAC"], "name": r["DIST_NAME"].strip(), "state": r["STATE_CODE"].strip()},
            "geometry": g,
        }
    )
with open(out, "w") as f:
    json.dump(
        {
            "type": "FeatureCollection",
            "features": features,
            "attribution": "Coastal waters zones © Commonwealth of Australia, "
            "Bureau of Meteorology (IDM00003)",
        },
        f,
        separators=(",", ":"),
    )
print(len(features), "zones")
