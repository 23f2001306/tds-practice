from __future__ import annotations

import csv
import io
import json
import re
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

EMAIL = "23f2001306@ds.study.iitm.ac.in"
QUIZ_SIGN = (
    "rBAu7cxq76iyxUcc0Yd1JLQOcqJPaVlXUcLOxkXFzCace9cXr3E2E3/"
    "a2cTs+BHpoHfpuaNRBL0fCzDFsSuWI83xIhN5cUNnqHcc1KP47iRtuGOBjXg5"
    "ESQL4TDzvQSG3Tn86+oIU5O3hpoS1uqpmbB47kKZ450R1Hy5KYHF5dIMDkH/"
    "dvHwlxTME7fxz01alHakdgoZInGxDZofDjtWIbvjQGEhpXl8rSfJB1R3uLZ7/"
    "uSHE6r51zW7qZmksvTVUBEdMaVV9waRG+nAG2UTni39q39eFGdNEQ31ErLR78"
    "ElGSmQqWXh8YFonMFUQQorgQageGJxBxtsYy9AcCZrSw=="
)
DATA_URL = "https://exam.sanand.workers.dev/questionData"
IST = ZoneInfo("Asia/Kolkata")

app = FastAPI(title="Acme Appliances Ledger Agent")


class Question(BaseModel):
    question: str


def source_url(path: str) -> str:
    return DATA_URL + "?" + urlencode(
        {
            "email": EMAIL,
            "quizSign": QUIZ_SIGN,
            "questionId": "q-ledger-agent-server",
            "path": path,
        }
    )


def fetch_text(path: str) -> str:
    request = Request(
        source_url(path),
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urlopen(request, timeout=10) as response:
        return response.read().decode("utf-8")


def parse_date(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(IST)


@lru_cache(maxsize=1)
def load_ledger() -> tuple[list[dict[str, Any]], dict[str, Decimal]]:
    latest: dict[str, dict[str, Any]] = {}
    for line in fetch_text("/export").splitlines():
        if not line.strip():
            continue
        order = json.loads(line)
        previous = latest.get(order["id"])
        if previous is None or parse_date(order["updated_at"]) > parse_date(
            previous["updated_at"]
        ):
            latest[order["id"]] = order

    rates_data = json.loads(fetch_text("/rates"))
    rates = {
        currency: Decimal(str(rate))
        for currency, rate in rates_data["usd_per_unit"].items()
    }
    return list(latest.values()), rates


def money(value: Decimal) -> float:
    rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float(rounded)


def month_number(text: str) -> int | None:
    months = {
        name.lower(): number
        for number, name in enumerate(
            (
                "",
                "January",
                "February",
                "March",
                "April",
                "May",
                "June",
                "July",
                "August",
                "September",
                "October",
                "November",
                "December",
            )
        )
        if name
    }
    for name, number in months.items():
        if re.search(rf"\b{name}\b", text, re.IGNORECASE):
            return number
    numeric = re.search(r"\b(20\d{2})[-/](\d{1,2})\b", text)
    return int(numeric.group(2)) if numeric else None


def filter_orders(question: str, orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lowered = question.lower()
    result = orders

    regions = ("north", "south", "east", "west", "central")
    region = next((item for item in regions if re.search(rf"\b{item}\b", lowered)), None)
    if region:
        result = [order for order in result if order["region"].lower() == region]

    products = sorted(
        {order["product"] for order in orders},
        key=len,
        reverse=True,
    )
    product = next(
        (item for item in products if item.lower() in lowered),
        None,
    )
    if product:
        result = [order for order in result if order["product"] == product]

    customer = re.search(r"\bC\d+\b", question, re.IGNORECASE)
    if customer:
        result = [
            order
            for order in result
            if order["customer"].lower() == customer.group(0).lower()
        ]

    year = re.search(r"\b(20\d{2})\b", question)
    month = month_number(question)
    if year or month:
        result = [
            order
            for order in result
            if (year is None or parse_date(order["created_at"]).year == int(year.group(1)))
            and (month is None or parse_date(order["created_at"]).month == month)
        ]
    return result


def choose_status(question: str) -> str:
    lowered = question.lower()
    if re.search(r"\b(refund|refunded|refunds)\b", lowered):
        return "refunded"
    if re.search(r"\b(void|voided|cancelled|canceled)\b", lowered):
        return "void"
    return "paid"


def answer_question(question: str) -> Any:
    orders, rates = load_ledger()
    filtered = filter_orders(question, orders)
    lowered = question.lower()
    status = choose_status(question)
    relevant = [order for order in filtered if order["status"] == status]

    def usd(order: dict[str, Any]) -> Decimal:
        return Decimal(str(order["amount"])) * rates[order["currency"]]

    is_count = any(word in lowered for word in ("how many", "count", "number of"))
    is_average = any(word in lowered for word in ("average", "avg", "mean"))
    asks_quantity = any(word in lowered for word in ("quantity", "units", "items"))
    asks_customer = "customer" in lowered
    asks_product = "product" in lowered
    asks_region = "region" in lowered
    asks_unique = "unique" in lowered or "distinct" in lowered
    asks_top = any(
        word in lowered
        for word in ("highest", "largest", "most", "top", "best", "maximum", "maximum")
    )

    if asks_top and (asks_customer or asks_product or asks_region):
        grouped: dict[str, Decimal] = defaultdict(Decimal)
        key = (
            "customer"
            if asks_customer
            else "product"
            if asks_product
            else "region"
        )
        for order in relevant:
            grouped[order[key]] += (
                Decimal(str(order["qty"])) if asks_quantity else usd(order)
            )
        if not grouped:
            return 0
        return max(grouped, key=grouped.get)

    if is_count:
        if asks_unique and asks_customer:
            return len({order["customer"] for order in relevant})
        if asks_unique and asks_product:
            return len({order["product"] for order in relevant})
        return len(relevant)

    if asks_quantity:
        total_quantity = sum(order["qty"] for order in relevant)
        return total_quantity / len(relevant) if is_average and relevant else total_quantity

    total = sum((usd(order) for order in relevant), Decimal("0"))
    if is_average:
        return money(total / len(relevant)) if relevant else 0
    return money(total)


@app.post("/")
@app.post("/api/answer")
def answer(payload: Question) -> dict[str, Any]:
    if not payload.question.strip():
        raise HTTPException(status_code=400, detail="question must not be empty")
    return {"answer": answer_question(payload.question)}
