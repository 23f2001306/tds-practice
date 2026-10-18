import json
import os
import re

import ollama
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator


app = FastAPI(title="Local LLM Invoice Extraction Service")

OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")


class ExtractRequest(BaseModel):
    text: str = Field(..., description="Free-form invoice text")

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("text must not be empty")
        return value


class InvoiceResponse(BaseModel):
    vendor: str
    amount: float
    currency: str
    date: str

    @field_validator("vendor")
    @classmethod
    def validate_vendor(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("vendor must not be empty")
        return value

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: float) -> float:
        if value < 0:
            raise ValueError("amount must not be negative")
        return value

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        value = value.strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", value):
            raise ValueError("currency must be a three-letter code")
        return value

    @field_validator("date")
    @classmethod
    def validate_date(cls, value: str) -> str:
        value = value.strip()
        if not re.search(r"\b\d{4}-\d{2}-\d{2}\b", value):
            raise ValueError("date must contain YYYY-MM-DD")
        return value


@app.post("/extract", response_model=InvoiceResponse)
def extract_invoice(request: ExtractRequest) -> InvoiceResponse:
    schema = InvoiceResponse.model_json_schema()

    try:
        result = ollama.chat(
            model=OLLAMA_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Extract the requested fields from the invoice text. "
                        "Copy the vendor name exactly, including punctuation and "
                        "random identifiers. Use the total amount due, not a "
                        "subtotal or tax. Use the payment due date. Return only "
                        "JSON that matches the supplied schema."
                    ),
                },
                {"role": "user", "content": request.text},
            ],
            format=schema,
            options={"temperature": 0},
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="The local Ollama model is unavailable",
        ) from exc

    try:
        content = result["message"]["content"]
        data = json.loads(content)
        return InvoiceResponse.model_validate(data)
    except (KeyError, TypeError, json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail="The invoice fields could not be extracted",
        ) from exc


@app.get("/")
def health_check() -> dict[str, str]:
    return {"status": "ok"}
