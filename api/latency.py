from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List
import json
import statistics
from pathlib import Path

app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=False,
)


class RequestBody(BaseModel):
    regions: List[str]
    threshold_ms: float


DATA_FILE = Path(__file__).resolve().parent.parent / "q-vercel-latency.json"

with open(DATA_FILE, "r") as f:
    telemetry = json.load(f)


@app.options("/api/latency")
async def options_latency():
    return JSONResponse(
        content={},
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        },
    )


@app.post("/api/latency")
async def get_metrics(request: Request, body: RequestBody):
    results = []

    for region in body.regions:
        records = [
            r for r in telemetry
            if r["region"] == region
        ]

        if not records:
            continue

        latencies = [r["latency_ms"] for r in records]
        uptimes = [r["uptime_pct"] for r in records]

        p95 = statistics.quantiles(
            latencies,
            n=100,
            method="inclusive"
        )[94]

        results.append({
            "region": region,
            "avg_latency": sum(latencies) / len(latencies),
            "p95_latency": p95,
            "avg_uptime": sum(uptimes) / len(uptimes),
            "breaches": sum(
                x > body.threshold_ms
                for x in latencies
            ),
        })

    return JSONResponse(
        content=results,
        headers={
            "Access-Control-Allow-Origin": "*"
        },
    )
