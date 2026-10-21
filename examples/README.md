# Examples

Four small apps showing how to use `bom_ftp.py` and `bom_db.py`. All are standard library except
`fastapi_app.py`. Each runs on live BOM data, or on [`samples/bom.json`](../samples/bom.json) where noted.
Run them from the repo root.

| Example | Shows | Needs a database |
|---|---|---|
| [`my_spot.py`](my_spot.py) | A terminal check of one place: nearest stations, the zone forecast, warnings | No |
| [`web_api.py`](web_api.py) + [`map.html`](map.html) | A web app: background collector, JSON API, live wind map | Yes (SQLite) |
| [`fastapi_app.py`](fastapi_app.py) | The same API inside a FastAPI app | Yes (SQLite) |
| [`alerts.py`](alerts.py) | Notifications for new warnings on your zones, or a windy station | Yes (SQLite) |

## my_spot.py: conditions at one place, no database

```sh
python examples/my_spot.py --find sydney                                  # find your zone code (or see docs/zones.md)
python examples/my_spot.py --lat -33.85 --lon 151.27 --zone NSW_MW009     # live
python examples/my_spot.py --lat -33.85 --lon 151.27 --zone TAS_MW004 --sample samples/bom.json
```

```
Nearest stations
  Sydney Harbour (1.2 km): NE 13 kn, gusting 15 at Wed 21:20
  Fort Denison (4.2 km): ENE 10 kn, gusting 14 at Wed 21:20
  North Head (4.7 km): ENE 9 kn, gusting 12 at Wed 21:20

Sydney Enclosed Waters forecast, issued Wed 16:05
  Wed 16:00: East to northeasterly 10 to 15 knots. Below 0.5 metres.
  Thu 00:00: Easterly 10 to 15 knots turning northeasterly 15 to 20 knots during the morning. Below 0.5 metres.
  ...
Warnings: none
```

This is the pattern when you only need what is current right now: call `bom_ftp.fetch_all()` and work on
the dicts.

## web_api.py: a web app with a map

```sh
python examples/web_api.py bom.sqlite                               # collects live every 5 minutes
python examples/web_api.py bom.sqlite --sample samples/bom.json    # offline
```

Open http://127.0.0.1:8000/ for the map: stations coloured by wind speed with popups, and the warnings in
force.

![Wind map](../docs/map.png)

The JSON API, every response wrapped as `{"attribution", "disclaimer", "data"}`:

| GET | Returns |
|---|---|
| `/api/stations?state=NSW` | Latest reading per station, windiest first |
| `/api/near?lat=-33.85&lon=151.27&km=20` | Stations near a point, with `km` |
| `/api/station/066214?hours=24` | One station's readings |
| `/api/zones?q=sydney` | Zones by name |
| `/api/forecast/NSW_MW009` | A zone's current forecast and synopsis |
| `/api/warnings?state=TAS` or `?zone=NSW_MW004` | Warnings in force |

The pattern:
- a collector thread with its own SQLite connection calls `fetch_all` + `save` + `prune` every 5 minutes;
- each request opens a connection and calls the `bom_db` query functions;
- query parameters are checked (numbers, ranges, codes) before they reach SQL, and bad input gets a 400.

All the routing lives in `route()`, so another framework can reuse it. It listens on 127.0.0.1 unless you pass
`--host 0.0.0.0`. Put it behind your normal reverse proxy before exposing it.

## fastapi_app.py: the same in FastAPI

```sh
BOM_DB=bom.sqlite uv run --with fastapi --with uvicorn uvicorn examples.fastapi_app:app
```

It starts the collector thread in FastAPI's lifespan and passes `/api/...` to `web_api.route()`. Flask,
Django and the rest work the same way: a background collector, and query functions in the handlers.

## alerts.py: notifications

```sh
python examples/alerts.py bom.sqlite --zone NSW_MW004 --zone NSW_MW009 --station 066196 --over 25
python examples/alerts.py bom.sqlite --zone TAS_MW004 --once      # one pass, for cron
```

```
Strong Wind Warning for coastal waters between Northern Tip of Flinders Island and South East Cape for 22 October (until Fri 00:00)
Sydney Harbour: 13 kn, gust 15 kn
```

It sends each warning once, and a station alert once each time the wind rises past `--over`. Set
`BOM_ALERT_WEBHOOK` to also POST each alert as `{"text": "..."}`. Slack, Discord, ntfy and Home Assistant
webhooks accept that shape. Keep the URL in the environment, never in code. Station ids come from
`/api/near` or `bom_db.find_stations()`.

## Other ideas

- **Home Assistant**: point a [RESTful sensor](https://www.home-assistant.io/integrations/sensor.rest/) at
  `/api/near?...` or `/api/warnings?zone=...` from `web_api.py`.
- **Grafana**: add the SQLite file as a data source and chart `observations` with `queries.sql` query 5.
- **Zones on the map**: build `bom_zones.geojson` with `tools/build_zones.py`, load it in `map.html` with
  `L.geoJSON`, and colour the zones listed in `/api/warnings`.
- **Model checks**: pair each reading with your own forecast for that place and time, to see how
  well each model is doing near you.

Whatever you build, show the `attribution` and `disclaimer`, and read the Terms section of the main README.
