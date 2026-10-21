"""Offline checks of the database store (no network): python test_bom_db.py, or pytest."""

import json
import pathlib
import re
import sqlite3

import bom_db

HERE = pathlib.Path(__file__).parent
NOW = 2_000_000_000.0


def pass_data(now, wind=10.0, warnings=True):
    return {
        "fetched": now,
        "stations": [
            {"id": "066214", "name": "Sydney - Observatory Hill", "state": "NSW", "lat": -33.86, "lon": 151.2,
             "height_m": 43.0, "url": None, "t": now - 60, "wind_kn": wind, "gust_kn": 15.0, "dir": 90.0,
             "temp_c": 20.0, "pressure_hpa": 1015.0},
            {"id": "086338", "name": "Melbourne (Olympic Park)", "state": "VIC", "lat": -37.83, "lon": 144.98,
             "height_m": 8.0, "url": None, "t": now - 60, "wind_kn": 4.0, "gust_kn": None, "dir": 180.0,
             "temp_c": 15.0, "pressure_hpa": 1020.0},
        ],
        "zones": {"NSW_MW005": {"name": "Sydney Coast", "product": "IDN11001", "issued": now - 600,
                                "parent": "NSW_ME001",
                                "periods": [{"start": now - 600, "end": now + 3600,
                                             "text": {"forecast_winds": "NE 15 to 20 knots."}},
                                            {"start": now - 9000, "end": now - 3600, "text": {"x": "old"}}]}},
        "synopsis": {"NSW_ME001": "A high in the Tasman."},
        "warnings": [{"id": "w1", "headline": "Gale Warning for Sydney Coast", "event": "Wind", "level": "gale",
                      "severity": "Severe", "effective": now - 600, "expires": now + 86400,
                      "zones": ["NSW_MW005", "NSW_MW006"], "url": None}] if warnings else [],
    }


def test_save_and_query():
    conn = bom_db.connect(":memory:")
    bom_db.save(conn, pass_data(NOW))
    bom_db.save(conn, pass_data(NOW))  # repeated pass: nothing doubled
    bom_db.save(conn, pass_data(NOW + 600, wind=20.0))
    assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 4

    top = bom_db.latest(conn, now=NOW + 600)
    assert [r["name"] for r in top] == ["Sydney - Observatory Hill", "Melbourne (Olympic Park)"]
    assert top[0]["wind_kn"] == 20.0
    assert [r["id"] for r in bom_db.latest(conn, state="VIC", now=NOW + 600)] == ["086338"]
    assert len(bom_db.history(conn, "066214", NOW - 3600, NOW + 600)) == 2
    assert bom_db.find_stations(conn, "olympic")[0]["id"] == "086338"
    near = bom_db.nearest(conn, -33.87, 151.21, km=20)
    assert [r["id"] for r in near] == ["066214"] and near[0]["km"] < 2

    f = bom_db.forecast(conn, "NSW_MW005", now=NOW)
    assert [p["texts"] for p in f["periods"]] == [{"forecast_winds": "NE 15 to 20 knots."}]  # ended one left out
    assert f["synopsis"] == "A high in the Tasman." and bom_db.forecast(conn, "XX_MW001") is None
    assert bom_db.search_zones(conn, "sydney")[0]["aac"] == "NSW_MW005"

    assert [w["zones"] for w in bom_db.active_warnings(conn, now=NOW)] == [["NSW_MW005", "NSW_MW006"]]
    assert len(bom_db.active_warnings(conn, aac="NSW_MW006", now=NOW)) == 1
    assert len(bom_db.active_warnings(conn, state="nsw", now=NOW)) == 1
    assert bom_db.active_warnings(conn, state="VIC", now=NOW) == []

    bom_db.save(conn, pass_data(NOW + 1200, warnings=False))  # gone from the feed before expiry
    assert bom_db.active_warnings(conn, now=NOW + 1200) == []
    assert bom_db.warnings_between(conn, NOW, NOW + 1200)[0]["withdrawn"] == NOW + 1200

    bad = pass_data(NOW + 1800)
    bad["zones"]["NSW_MW005"]["periods"].append({"start": None, "end": None, "text": {"x": "y"}})
    bom_db.save(conn, bad)  # a malformed period is skipped, the rest of the pass is still saved
    assert conn.execute("SELECT MAX(t) FROM observations").fetchone()[0] == NOW + 1800 - 60

    bom_db.prune(conn, days=1, now=NOW + 3 * 86400)
    assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM warnings").fetchone()[0] == 0


def test_sample_loads():
    conn = bom_db.connect(":memory:")
    data = json.loads((HERE / "samples" / "bom.json").read_text(encoding="utf-8"))
    bom_db.save(conn, data)
    assert len(bom_db.latest(conn, now=data["fetched"])) == len(data["stations"])
    assert conn.execute("SELECT COUNT(*) FROM zones").fetchone()[0] == len(data["zones"])
    assert len(bom_db.active_warnings(conn, now=data["fetched"])) == len(data["warnings"])


def test_queries_sql_runs():
    """Every query in queries.sql parses and runs against the sample."""
    conn = bom_db.connect(":memory:")
    bom_db.save(conn, json.loads((HERE / "samples" / "bom.json").read_text(encoding="utf-8")))
    sql = re.sub(r"--[^\n]*", "", (HERE / "queries.sql").read_text())
    params = {"now": NOW, "since": 0, "until": NOW, "station": "066214", "aac": "NSW_MW005", "state": "NSW"}
    for q in filter(str.strip, sql.split(";")):
        try:
            conn.execute(q, params).fetchall()
        except sqlite3.Error as e:
            raise AssertionError(f"{e}: {q.strip()[:80]}")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
