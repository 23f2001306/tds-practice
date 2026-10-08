from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List
import json
import statistics
from pathlib import Path

app = FastAPI()

# Allow POST requests from any origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["*"],
)

# Load telemetry bundled with the deployment
DATA_FILE = Path(__file__).resolve().parent.parent / "q-vercel-latency.json"

with open(DATA_FILE, "r") as f:
    telemetry = json.load(f)


class RequestBody(BaseModel):
    regions: List[str]
    threshold_ms: float


@app.post("/")
async def get_metrics(request: RequestBody):
    results = []

    for region in request.regions:
        records = [
            record
            for record in telemetry
            if record["region"] == region
        ]

        if not records:
            continue

        latencies = [record["latency_ms"] for record in records]
        uptimes = [record["uptime_pct"] for record in records]

        # p95 using linear interpolation
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
                latency > request.threshold_ms
                for latency in latencies
            ),
        })

    return results
