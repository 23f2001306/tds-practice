from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
import httpx

app = FastAPI()
client = httpx.AsyncClient(timeout=120.0)

@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def proxy(path: str, request: Request):
    if path == "v1" or path.startswith("v1/"):
        base = "http://127.0.0.1:11434"
        forwarded_path = "/" + path

    elif path == "extract" or path.startswith("extract/"):
        base = "http://127.0.0.1:8001"
        forwarded_path = "/" + path

    else:
        raise HTTPException(404, "Unknown route")

    url = base + forwarded_path
    if request.url.query:
        url += "?" + request.url.query

    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in {
            "host", "connection", "content-length",
            "transfer-encoding", "accept-encoding"
        }
    }

    upstream_request = client.build_request(
        request.method,
        url,
        headers=headers,
        content=await request.body(),
    )
    response = await client.send(upstream_request, stream=True)

    excluded = {
        "connection", "transfer-encoding", "content-length",
        "content-encoding", "keep-alive"
    }
    response_headers = {
        k: v for k, v in response.headers.items()
        if k.lower() not in excluded
    }

    return StreamingResponse(
        response.aiter_raw(),
        status_code=response.status_code,
        headers=response_headers,
        background=BackgroundTask(response.aclose),
    )