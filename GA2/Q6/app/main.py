from contextlib import asynccontextmanager
import os

from fastapi import FastAPI
from redis.asyncio import Redis


redis: Redis | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis
    redis = Redis.from_url(
        os.getenv("REDIS_URL", "redis://redis:6379/0"),
        decode_responses=True,
    )
    yield
    await redis.aclose()
    redis = None


app = FastAPI(lifespan=lifespan)


def get_redis() -> Redis:
    if redis is None:
        raise RuntimeError("Redis client is not initialized")
    return redis


@app.post("/hit/{key}")
async def hit(key: str) -> dict[str, str | int]:
    count = await get_redis().incr(key)
    return {"key": key, "count": count}


@app.get("/count/{key}")
async def count(key: str) -> dict[str, str | int]:
    value = await get_redis().get(key)
    return {"key": key, "count": int(value) if value is not None else 0}


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    await get_redis().ping()
    return {"status": "ok", "redis": "up"}
