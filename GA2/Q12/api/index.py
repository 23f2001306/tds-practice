import math
import threading
import time
import uuid
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse


EMAIL = "23f2001306@ds.study.iitm.ac.in"
RATE_LIMIT = 9
WINDOW_SECONDS = 10.0

# The exam page origin is included so browser-based verification can call /ping.
ALLOWED_ORIGINS = [
    "https://app-3q7ydz.example.com",
    "https://exam.s-anand.net",
    "https://exam.sanand.workers.dev"
]

app = FastAPI(title="Middleware Stack API")

request_times: Dict[str, Deque[float]] = defaultdict(deque)
rate_limit_lock = threading.Lock()


def check_rate_limit(client_id: str) -> int:
    now = time.monotonic()

    with rate_limit_lock:
        timestamps = request_times[client_id]

        while timestamps and now - timestamps[0] >= WINDOW_SECONDS:
            timestamps.popleft()

        if len(timestamps) >= RATE_LIMIT:
            return max(1, math.ceil(WINDOW_SECONDS - (now - timestamps[0])))

        timestamps.append(now)
        return 0


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    request_id = (
        getattr(request.state, "request_id", None)
        or request.headers.get("X-Request-ID")
        or str(uuid.uuid4())
    )
    request.state.request_id = request_id

    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["X-Request-ID", "X-Client-Id", "Content-Type"],
    expose_headers=["X-Request-ID", "Retry-After"],
)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)

    client_id = request.headers.get("X-Client-Id", "anonymous")
    retry_after = check_rate_limit(client_id)

    if retry_after:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        origin = request.headers.get("Origin")
        headers = {
            "Retry-After": str(retry_after),
            "X-Request-ID": request_id,
        }
        if origin in ALLOWED_ORIGINS:
            headers["Access-Control-Allow-Origin"] = origin
            headers["Access-Control-Expose-Headers"] = "X-Request-ID, Retry-After"
            headers["Vary"] = "Origin"

        return JSONResponse(
            status_code=429,
            content={
                "detail": "Rate limit exceeded",
                "request_id": request_id,
            },
            headers=headers,
        )

    return await call_next(request)


@app.get("/ping")
async def ping(request: Request):
    return {
        "email": EMAIL,
        "request_id": request.state.request_id,
    }
