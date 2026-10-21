"""Offline checks of the parsers (no network): python test_bom_ftp.py, or pytest."""

import time

import bom_ftp as bom


def test_observations():
    obs = b"""<product><observations>
        <station wmo-id="94768" bom-id="066214" lat="-33.86" lon="151.2" description="Sydney">
        <period time-utc="2026-10-16T11:30:00+00:00"><level>
        <element units="knots" type="wind_spd">12</element>
        <element units="deg" type="wind_dir_deg">109</element>
        <element units="km/h" type="wind_gust_spd">99</element>
        </level></period></station></observations></product>"""
    s = bom.parse_observations(obs, "N")[0]
    assert s["wind_kn"] == 12 and s["dir"] == 109 and s["gust_kn"] is None  # wrong units are dropped
    assert s["state"] == "NSW" and s["url"].endswith("IDN60901.94768.shtml")


def test_forecast():
    fc = b"""<product><amoc><identifier>IDN11001</identifier>
        <issue-time-utc>2026-10-16T06:00:00Z</issue-time-utc></amoc><forecast>
        <area aac="NSW_ME001" type="region" description="New South Wales">
        <forecast-period><text type="synoptic_situation">A high in the Tasman.</text></forecast-period></area>
        <area aac="NSW_MW005" type="coast" description="Sydney Coast" parent-aac="NSW_ME001">
        <forecast-period start-time-utc="2026-10-16T13:00:00Z" end-time-utc="2026-10-17T13:00:00Z">
        <text type="forecast_winds">Easterly 10 knots.</text></forecast-period>
        <forecast-period start-time-utc="2026-10-16T06:00:00Z" end-time-utc="2026-10-16T13:00:00Z">
        <text type="forecast_winds">Northeasterly 15 to 20 knots.</text><text type="forecast_seas"> </text>
        </forecast-period></area>
        <area aac="NSW_MW009" type="coast" description="Sydney Enclosed Waters" parent-aac="NSW_ME001">
        <forecast-period start-time-utc="2026-10-16T07:00:00Z" end-time-utc="2026-10-20T13:00:00Z">
        <text type="synoptic_situation">Northeasterlies over Sydney Waters.</text></forecast-period>
        <forecast-period start-time-utc="2026-10-16T06:00:00Z" end-time-utc="2026-10-16T13:00:00Z">
        <text type="forecast_winds">Northeast 10 knots.</text></forecast-period></area></forecast></product>"""
    zones, syn = bom.parse_forecast(fc)
    z = zones["NSW_MW005"]
    assert z["product"] == "IDN11001" and z["parent"] == "NSW_ME001"
    assert z["periods"][0]["text"] == {"forecast_winds": "Northeasterly 15 to 20 knots."}  # sorted, blank dropped
    # A local waters zone's own synopsis is a synopsis, not a forecast period.
    assert [p["text"] for p in zones["NSW_MW009"]["periods"]] == [{"forecast_winds": "Northeast 10 knots."}]
    assert syn == {"NSW_ME001": "A high in the Tasman.", "NSW_MW009": "Northeasterlies over Sydney Waters."}


def test_warnings():
    cap = b"""<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
        <identifier>x</identifier><status>Actual</status>
        <info><headline>Gale Warning for Sydney Coast</headline>
        <expires>2099-01-01T00:00:00+00:00</expires>
        <area><geocode><valueName>AMOC-AreaCode</valueName><value>NSW_MW004</value></geocode></area></info>
        <info><headline>Cancellation of Strong Wind Warning</headline>
        <expires>2099-01-01T00:00:00+00:00</expires>
        <area><geocode><valueName>AMOC-AreaCode</valueName><value>NSW_MW005</value></geocode></area></info>
        </alert>"""
    w = bom.parse_warning(cap, time.time())
    assert [(x["level"], x["zones"]) for x in w] == [("gale", ["NSW_MW004"])]
    assert bom.parse_warning(cap, 5e9) == []  # expired
    out = bom.parse_all({"IDN21000.cap.xml": cap, "IDN60920.xml": b"<broken"})
    assert len(out["warnings"]) == 1 and out["stations"] == []  # a bad file is skipped, not fatal


def test_rejects_dtd():
    try:
        bom.parse_warning(b'<!DOCTYPE x [<!ENTITY a "b">]><x/>', 0)
        raise AssertionError("DTD accepted")
    except ValueError:
        pass


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
