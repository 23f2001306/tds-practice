import json
import logging
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from threading import Lock

from fastapi import FastAPI, Query, Request
from fastapi.responses import PlainTextResponse


app = FastAPI(title="Production Observability Demo")
started_at = time.monotonic()
counter_lock = Lock()
http_requests_total = 0
request_logs = deque(maxlen=1000)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = getattr(record, "structured_entry", None)
        if entry is None:
            entry = {
                "level": record.levelname.lower(),
                "ts": datetime.now(timezone.utc).isoformat(),
                "message": record.getMessage(),
            }
        return json.dumps(entry)


logger = logging.getLogger("observability")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)


@app.middleware("http")
async def observe_requests(request: Request, call_next):
    global http_requests_total

    request_id = str(uuid.uuid4())
    with counter_lock:
        http_requests_total += 1

    response = await call_next(request)
    entry = {
        "level": "info",
        "ts": datetime.now(timezone.utc).isoformat(),
        "path": request.url.path,
        "request_id": request_id,
    }
    request_logs.append(entry)
    log_record = logger.makeRecord(
        logger.name,
        logging.INFO,
        __file__,
        0,
        "request completed",
        (),
        None,
    )
    log_record.structured_entry = entry
    logger.handle(log_record)
    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/work")
async def do_work(n: int = Query(..., ge=0)):
    for _ in range(n):
        pass
    return {"email": "23f2001306@ds.study.iitm.ac.in", "done": n}


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics():
    with counter_lock:
        count = http_requests_total
    body = (
        "# HELP http_requests_total Total HTTP requests handled.\n"
        "# TYPE http_requests_total counter\n"
        f"http_requests_total {count}\n"
    )
    return PlainTextResponse(body, media_type="text/plain; version=0.0.4")


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "uptime_s": float(time.monotonic() - started_at)}


@app.get("/logs/tail")
async def logs_tail(limit: int = Query(10, ge=0)):
    return list(request_logs)[-limit:]
