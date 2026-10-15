import hashlib
import json
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

EMAIL = "23f2001306@ds.study.iitm.ac.in"
PROTOCOL_VERSION = "2024-11-05"

app = FastAPI(title="Exam MCP Server")


def jsonrpc_result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def jsonrpc_error(
    request_id: Any, code: int, message: str
) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


async def handle_mcp(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return JSONResponse(
            jsonrpc_error(None, -32700, "Request body must be valid JSON"),
            status_code=400,
        )

    if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
        return JSONResponse(
            jsonrpc_error(
                payload.get("id") if isinstance(payload, dict) else None,
                -32600,
                "Invalid JSON-RPC request",
            ),
            status_code=400,
        )

    request_id = payload.get("id")
    method = payload.get("method")

    if method == "notifications/initialized":
        return JSONResponse({}, status_code=202)

    if method == "initialize":
        params = payload.get("params")
        requested_version = (
            params.get("protocolVersion")
            if isinstance(params, dict)
            else None
        )
        protocol_version = (
            requested_version
            if isinstance(requested_version, str)
            else PROTOCOL_VERSION
        )
        return JSONResponse(
            jsonrpc_result(
                request_id,
                {
                    "protocolVersion": protocol_version,
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "exam-challenge-server",
                        "version": "1.0.0",
                    },
                },
            )
        )

    if method == "tools/list":
        return JSONResponse(
            jsonrpc_result(
                request_id,
                {
                    "tools": [
                        {
                            "name": "solve_challenge",
                            "description": (
                                "Hash the challenge supplied in the request headers."
                            ),
                            "inputSchema": {
                                "type": "object",
                                "properties": {},
                                "required": [],
                            },
                        }
                    ]
                },
            )
        )

    if method == "tools/call":
        params = payload.get("params")
        if not isinstance(params, dict) or params.get("name") != "solve_challenge":
            return JSONResponse(
                jsonrpc_error(request_id, -32602, "Unknown tool"),
                status_code=400,
            )

        challenge = request.headers.get("x-exam-challenge")
        if not challenge:
            return JSONResponse(
                jsonrpc_error(
                    request_id,
                    -32602,
                    "Missing X-Exam-Challenge request header",
                ),
                status_code=400,
            )

        digest = hashlib.sha256(f"{challenge}:{EMAIL}".encode("utf-8")).hexdigest()
        return JSONResponse(
            jsonrpc_result(
                request_id,
                {"content": [{"type": "text", "text": digest[:16]}]},
            )
        )

    return JSONResponse(
        jsonrpc_error(request_id, -32601, f"Method not found: {method}"),
        status_code=404,
    )


@app.post("/api/mcp")
@app.post("/mcp")
@app.post("/")
async def mcp_endpoint(request: Request) -> JSONResponse:
    return await handle_mcp(request)
