import time
import uuid

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware


ALLOWED_ORIGIN = "https://dash-z9kknz.example.com"
EMAIL = "23f2001306@ds.study.iitm.ac.in"

app = FastAPI(title="Descriptive Statistics API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_request_headers(request: Request, call_next):
    request_id = str(uuid.uuid4())
    started_at = time.perf_counter()
    response = await call_next(request)
    elapsed = max(0.0, time.perf_counter() - started_at)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time"] = f"{elapsed:.9f}"
    return response


@app.get("/stats")
async def stats(values: str = Query(..., description="Comma-separated integers")):
    raw_values = [value.strip() for value in values.split(",")]
    if not raw_values or any(not value for value in raw_values):
        raise HTTPException(status_code=400, detail="values must be comma-separated integers")

    try:
        numbers = [int(value) for value in raw_values]
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="values must be comma-separated integers",
        ) from exc

    total = sum(numbers)
    return {
        "email": EMAIL,
        "count": len(numbers),
        "sum": total,
        "min": min(numbers),
        "max": max(numbers),
        "mean": total / len(numbers),
    }
