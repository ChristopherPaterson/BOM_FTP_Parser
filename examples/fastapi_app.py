"""The same API as web_api.py in FastAPI, for apps that already use it. Reuses web_api.route(), so the
endpoints and checks are identical.

    pip install fastapi uvicorn      (or: uv run --with fastapi --with uvicorn ...)
    BOM_DB=bom.sqlite uvicorn examples.fastapi_app:app
"""

import os
import pathlib
import sys
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import bom_db  # noqa: E402
import web_api  # noqa: E402

DB = os.environ.get("BOM_DB", "bom.sqlite")


@asynccontextmanager
async def lifespan(app):
    threading.Thread(target=web_api.collect, args=(DB,), daemon=True).start()
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/")
def index():
    return FileResponse(HERE / "map.html")


@app.get("/api/{rest:path}")
def api(rest: str, request: Request):
    conn = bom_db.connect(DB)
    try:
        q = {k: request.query_params.getlist(k) for k in request.query_params}
        status, body = web_api.route(conn, "/api/" + rest, q, time.time())
    except web_api.BadRequest as e:
        status, body = 400, {"error": str(e)}
    finally:
        conn.close()
    return JSONResponse({**web_api.META, "data": body} if status == 200 else body, status)
