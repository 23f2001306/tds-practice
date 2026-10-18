import base64
import math
import threading
import time
import uuid
from collections import defaultdict, deque
from typing import Any, Deque, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel


TOTAL_ORDERS = 49
RATE_LIMIT = 15
WINDOW_SECONDS = 10.0

app = FastAPI(title="Orders API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class OrderCreate(BaseModel):
    item: Optional[str] = None
    quantity: Optional[int] = None


idempotency_orders: Dict[str, Dict[str, Any]] = {}
request_times: Dict[str, Deque[float]] = defaultdict(deque)
state_lock = threading.Lock()


def enforce_rate_limit(client_id: str) -> Optional[int]:
    now = time.monotonic()
    with state_lock:
        timestamps = request_times[client_id]
        while timestamps and now - timestamps[0] >= WINDOW_SECONDS:
            timestamps.popleft()

        if len(timestamps) >= RATE_LIMIT:
            return max(1, math.ceil(WINDOW_SECONDS - (now - timestamps[0])))

        timestamps.append(now)
    return None


@app.middleware("http")
async def rate_limit_requests(request: Request, call_next):
    client_id = request.headers.get("X-Client-Id", "anonymous")
    retry_after = enforce_rate_limit(client_id)
    if retry_after is not None:
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded"},
            headers={
                "Retry-After": str(retry_after),
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Expose-Headers": "Retry-After",
                "Cache-Control": "no-store",
            },
        )
    return await call_next(request)


def encode_cursor(next_id: int) -> str:
    return base64.urlsafe_b64encode(str(next_id).encode()).decode().rstrip("=")


def decode_cursor(cursor: Optional[str]) -> int:
    if not cursor:
        return 1
    try:
        padding = "=" * (-len(cursor) % 4)
        value = int(base64.urlsafe_b64decode((cursor + padding).encode()).decode())
    except (ValueError, UnicodeDecodeError, base64.binascii.Error) as exc:
        raise HTTPException(status_code=400, detail="Invalid cursor") from exc
    if not 1 <= value <= TOTAL_ORDERS + 1:
        raise HTTPException(status_code=400, detail="Invalid cursor")
    return value


@app.get("/orders")
def list_orders(limit: int = 10, cursor: Optional[str] = None):
    if limit < 1:
        raise HTTPException(status_code=400, detail="limit must be positive")

    start = decode_cursor(cursor)
    ids = list(range(start, min(start + limit, TOTAL_ORDERS + 1)))
    next_id = start + len(ids)
    return {
        "items": [{"id": order_id, "status": "catalog"} for order_id in ids],
        "next_cursor": encode_cursor(next_id) if next_id <= TOTAL_ORDERS else None,
    }


@app.post("/orders", status_code=201)
def create_order(
    payload: OrderCreate,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")

    with state_lock:
        existing = idempotency_orders.get(idempotency_key)
        if existing is not None:
            return existing

        order = {
            "id": str(uuid.uuid4()),
            "item": payload.item,
            "quantity": payload.quantity,
        }
        idempotency_orders[idempotency_key] = order
        return order
