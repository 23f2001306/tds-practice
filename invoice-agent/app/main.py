from __future__ import annotations

import json
import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import Response
from fastapi.exceptions import RequestValidationError

from .ai import decide_batch
from .db import IdempotencyConflict, TaskConflict, db
from .models import (
    MEDIA_A2A_JSON, MEDIA_BATCH_INPUT, MEDIA_PROPOSALS, MEDIA_RECEIPTS,
    MEDIA_RESULTS_INPUT, TASK_STATE_CANCELED, TASK_STATE_COMPLETED,
    TASK_STATE_INPUT_REQUIRED,
)
from .utils import hash_message, new_id

app = FastAPI(title="Invoice Safe Action Agent", version="1.0.0")


def principal(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer ") or not authorization[7:].strip():
        raise HTTPException(401, "Missing or malformed Authorization Bearer token")
    return authorization[7:].strip()


def version(value: str | None) -> None:
    if value and value != "1.0":
        raise HTTPException(400, "Expected A2A-Version: 1.0")


def a2a(data: Any, code: int = 200) -> Response:
    return Response(
        json.dumps(data, separators=(",", ":")),
        code,
        media_type=MEDIA_A2A_JSON,
        headers={"A2A-Version": "1.0"},
    )


@app.exception_handler(HTTPException)
async def a2a_http_error(_: Request, exc: HTTPException) -> Response:
    return a2a({"error": {"code": exc.status_code, "message": str(exc.detail)}}, exc.status_code)


@app.exception_handler(RequestValidationError)
async def a2a_validation_error(_: Request, exc: RequestValidationError) -> Response:
    return a2a({"error": {"code": 400, "message": "Invalid request", "details": exc.errors()}}, 400)


def compact_message(message: dict[str, Any]) -> dict[str, Any]:
    compact = dict(message)
    compact_parts = []
    for part in message.get("parts", []):
        copied = dict(part)
        data = copied.get("data")
        if isinstance(data, dict) and isinstance(data.get("packages"), list):
            copied["data"] = dict(data)
            copied["data"]["packages"] = [
                {"packageId": item.get("packageId")} if isinstance(item, dict) else item
                for item in data["packages"]
            ]
        compact_parts.append(copied)
    compact["parts"] = compact_parts
    return compact


def base_url(request: Request) -> str:
    configured = os.getenv("BASE_URL") or (
        request.app.state.base_url if hasattr(request.app.state, "base_url") else None
    )
    if configured:
        return configured.rstrip("/") + "/a2a/"
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    return f"{scheme}://{request.headers.get('host', request.url.netloc)}/a2a/"


@app.get("/.well-known/agent-card.json")
def agent_card(request: Request) -> dict:
    return {
        "name": "Invoice Safe Action Agent",
        "description": "Evidence-based invoice decisions with approval-gated execution and durable state.",
        "version": "1.0.0",
        "capabilities": {},
        "defaultInputModes": [MEDIA_BATCH_INPUT],
        "defaultOutputModes": [MEDIA_PROPOSALS, MEDIA_RECEIPTS],
        "skills": [{"id": "invoice_action_agent", "name": "Invoice Action Agent",
                    "description": "Reads invoice packages, cites evidence, proposes safe actions, and executes only accepted receipts.",
                    "tags": ["finance", "invoices", "evidence", "payments"]}],
        "supportedInterfaces": [{"url": base_url(request), "protocolBinding": "HTTP+JSON", "protocolVersion": "1.0"}],
    }


async def send(request: Request, authorization: str | None, a2a_version: str | None) -> Response:
    owner = principal(authorization)
    version(a2a_version)
    if request.headers.get("content-type", "").split(";")[0].lower() != MEDIA_A2A_JSON:
        raise HTTPException(415, "Content-Type must be application/a2a+json")
    body = await request.json()
    message = body.get("message")
    if not isinstance(message, dict) or not message.get("messageId"):
        raise HTTPException(400, "message.messageId is required")
    message_hash = hash_message(message)
    try:
        replay = db.idempotent(owner, message["messageId"], message_hash)
    except IdempotencyConflict:
        raise HTTPException(409, "IDEMPOTENCY_CONFLICT")
    if replay:
        return a2a({"task": replay})
    parts = message.get("parts")
    if not isinstance(parts, list) or not parts or not isinstance(parts[0], dict):
        raise HTTPException(400, "message.parts must be a non-empty list")
    part = parts[0]
    media = part.get("mediaType")
    data = part.get("data") or {}
    if media == MEDIA_BATCH_INPUT:
        proposals = await decide_batch(data.get("packages", []), data.get("policyRevision", ""))
        task = {
            "id": new_id("task"), "contextId": new_id("ctx"), "state": TASK_STATE_INPUT_REQUIRED,
            "history": [compact_message(message)],
            "artifacts": [{"mediaType": MEDIA_PROPOSALS, "data": {
                "batchId": data.get("batchId", "batch_default"),
                "proposals": [p.model_dump() for p in proposals],
            }}],
        }
        try:
            saved = db.create(task, owner, message["messageId"], message_hash)
        except IdempotencyConflict:
            raise HTTPException(409, "IDEMPOTENCY_CONFLICT")
        return a2a({"task": saved})
    if media != MEDIA_RESULTS_INPUT:
        raise HTTPException(400, f"Unsupported input mediaType: {media}")
    task_id, context_id = message.get("taskId"), message.get("contextId")
    if not task_id or not context_id:
        raise HTTPException(400, "Continuation requires taskId and contextId")
    task = db.get(task_id, owner)
    if not task:
        raise HTTPException(404, "Task not found")
    if task["contextId"] != context_id:
        raise HTTPException(400, "Mismatched contextId")
    if task["state"] in (TASK_STATE_COMPLETED, TASK_STATE_CANCELED):
        raise HTTPException(409, "Task is already terminal")
    proposal_data = task["artifacts"][0]["data"]
    if data.get("batchId") != proposal_data.get("batchId"):
        raise HTTPException(400, "Mismatched batchId")
    by_package = {p["packageId"]: p for p in proposal_data["proposals"]}
    executions = []
    for result in data.get("results", []):
        proposal = by_package.get(result.get("packageId"))
        if not proposal or result.get("actionId") != proposal["actionId"] or result.get("action") != proposal["action"]:
            raise HTTPException(400, "Result does not match stored proposal")
        if result.get("outcome") == "ACCEPTED":
            if not result.get("receiptNonce"):
                raise HTTPException(400, "Accepted result requires receiptNonce")
            executions.append({
                "packageId": proposal["packageId"], "actionId": proposal["actionId"],
                "action": proposal["action"], "receiptNonce": result["receiptNonce"],
                "facts": proposal["facts"], "evidenceRefs": proposal["evidenceRefs"],
            })
    receipts = {"mediaType": MEDIA_RECEIPTS, "data": {"batchId": proposal_data["batchId"], "executions": executions}}
    try:
        completed = db.complete(
            task_id,
            owner,
            task["history"] + [compact_message(message)],
            [task["artifacts"][0], receipts],
            message["messageId"],
            message_hash,
        )
    except (KeyError, TaskConflict):
        raise HTTPException(409, "Task is already terminal")
    return a2a({"task": completed})


@app.post("/a2a/message:send")
@app.post("/message:send")
async def message_send(request: Request, authorization: str | None = Header(None),
                       a2a_version: str | None = Header(None, alias="A2A-Version")) -> Response:
    return await send(request, authorization, a2a_version)


def task_get(task_id: str, authorization: str | None, a2a_version: str | None) -> Response:
    owner = principal(authorization)
    version(a2a_version)
    task = db.get(task_id, owner)
    if not task:
        raise HTTPException(404, "Task not found")
    return a2a(task)


@app.get("/a2a/tasks/{task_id}")
@app.get("/tasks/{task_id}")
def get_task(task_id: str, authorization: str | None = Header(None),
             a2a_version: str | None = Header(None, alias="A2A-Version")) -> Response:
    return task_get(task_id, authorization, a2a_version)


@app.get("/a2a/tasks")
@app.get("/tasks")
def list_tasks(authorization: str | None = Header(None),
               a2a_version: str | None = Header(None, alias="A2A-Version")) -> Response:
    owner = principal(authorization)
    version(a2a_version)
    return a2a({"tasks": db.list(owner)})


@app.post("/a2a/tasks/{task_id}:cancel")
@app.post("/tasks/{task_id}:cancel")
def cancel_task(task_id: str, authorization: str | None = Header(None),
                a2a_version: str | None = Header(None, alias="A2A-Version")) -> Response:
    owner = principal(authorization)
    version(a2a_version)
    try:
        return a2a(db.cancel(task_id, owner))
    except KeyError:
        raise HTTPException(404, "Task not found")
    except TaskConflict:
        raise HTTPException(409, "Task is already terminal")
