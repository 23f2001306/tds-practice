from typing import Any, Literal

from pydantic import BaseModel, Field

ROLE_USER = "ROLE_USER"
TASK_STATE_INPUT_REQUIRED = "TASK_STATE_INPUT_REQUIRED"
TASK_STATE_COMPLETED = "TASK_STATE_COMPLETED"
TASK_STATE_CANCELED = "TASK_STATE_CANCELED"
MEDIA_A2A_JSON = "application/a2a+json"
MEDIA_BATCH_INPUT = "application/vnd.ga5.invoice-claim-batch+json"
MEDIA_PROPOSALS = "application/vnd.ga5.invoice-action-proposals+json"
MEDIA_RESULTS_INPUT = "application/vnd.ga5.invoice-action-results+json"
MEDIA_RECEIPTS = "application/vnd.ga5.invoice-action-receipts+json"
ActionType = Literal[
    "settle_invoice",
    "request_approval",
    "hold_invoice",
    "reject_duplicate",
    "open_exception",
]


class Facts(BaseModel):
    vendorName: str
    invoiceNumber: str
    amountMinor: int
    currency: str


class Proposal(BaseModel):
    packageId: str
    actionId: str
    action: ActionType
    facts: Facts
    evidenceRefs: list[str]
    rationale: str


class MessageSendRequest(BaseModel):
    message: dict[str, Any]
    configuration: dict[str, Any] | None = None


class Task(BaseModel):
    id: str
    contextId: str
    state: str
    history: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
