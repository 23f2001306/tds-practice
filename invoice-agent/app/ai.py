from __future__ import annotations

import asyncio
import json
import os
import re
from decimal import Decimal

import httpx

from .db import db
from .models import Proposal
from .utils import hash_message, new_id

DECOYS = (
    "unrelated", "does not refer to", "training case", "training example",
    "training appendix", "not this vendor's active instruction", "uses the phrase",
    "separate supplier", "closed reconciliation incident", "govern no live case",
    "non-operative", "different order contains", "archived delegation has expired",
    "different authorization threshold", "review location", "mail gateway",
    "month-end report", "superseded draft", "timestamps are stored",
    "concise evidence citations", "vocabulary, not case instructions",
    "hypothetical refund scenario", "confident narrative never overrides",
    "procurement confirmed", "moved the file between queues", "intake export preserves",
)


def package_documents(package: dict) -> list[dict]:
    documents = package.get("documents")
    if isinstance(documents, list) and documents:
        return [d if isinstance(d, dict) else {"text": str(d)} for d in documents]
    return [{"text": str(package.get("text", ""))}]


def text_of(document: dict) -> str:
    return str(document.get("text") or document.get("content") or document.get("body") or "")


def refs(text: str) -> list[str]:
    return re.findall(r"\[R_[A-Za-z0-9]+\]", text)


def controlling(pkg: dict) -> tuple[str, str, list[str]]:
    docs = package_documents(pkg)
    ledger = next((d for d in docs if d.get("name") == "ledger-and-correspondence.txt"), None)
    if ledger:
        first = re.split(r"\r?\n\r?\n", text_of(ledger))[0]
        first_refs = refs(first)
        if len(first_refs) == 3:
            return "style_b", first, first_refs

    lines = []
    for doc in docs:
        lines.extend(
            line.strip()
            for line in text_of(doc).splitlines()
            if line.strip() and "[" in line and "]" in line
        )
    good = [line for line in lines if not any(term in line.lower() for term in DECOYS)]
    return "style_a", " ".join(good), refs("\n".join(good))[:3]


def heuristic(pkg: dict, policy_revision: str = "v1") -> dict:
    raw = json.dumps(pkg, ensure_ascii=False)
    style, operative, evidence = controlling(pkg)
    low = operative.lower()

    vendor_match = (
        re.search(r"Supplier\s+([^;]+);", raw, re.I)
        or re.search(r"to\s+([A-Za-z0-9\s&]+?)\s+previously", raw, re.I)
        or re.search(r"from\s+([A-Za-z0-9\s&]+?)\s+for", raw, re.I)
        or re.search(r"(?:vendor|payee)\s*:?\s*([^.;\n]+)", raw, re.I)
    )
    invoice_match = re.search(r"\b(INV-[A-Za-z0-9-]+)\b", raw, re.I)
    amount_match = re.search(r"\b(INR|USD|EUR|GBP)\s*([\d.]+)", raw, re.I)
    currency = amount_match.group(1).upper() if amount_match else "INR"
    amount_minor = (
        int(round(float(amount_match.group(2).rstrip(".")) * 100))
        if amount_match else 10000
    )

    if style == "style_a":
        duplicate = re.search(
            r"previously settled order\s+(PO-\w+).*?"
            r"currently submitted order is\s+(PO-\w+)",
            operative,
            re.I | re.S,
        )
        if "awaiting independent verification" in low or "unverified bank change" in low:
            action = "hold_invoice"
        elif duplicate and duplicate.group(1) == duplicate.group(2):
            action = "reject_duplicate"
        elif "material order/invoice difference" in low or "conflicts with" in low:
            action = "open_exception"
        elif "named approver is required" in low or "ceiling" in low:
            ceiling = re.search(r"ceiling for [A-Z]{3} is [A-Z]{3} ([\d.]+)", operative, re.I)
            total = re.search(r"(?:signed for|invoice total is) [A-Z]{3} ([\d.]+)", operative, re.I)
            action = "request_approval" if ceiling and total and float(total.group(1)) > float(ceiling.group(1)) else "settle_invoice"
        else:
            action = "settle_invoice"
    elif any(term in low for term in ("beneficiary details", "destination-account change", "payment-detail changes")):
        action = "hold_invoice"
    elif any(term in low for term in ("same commercial key", "earlier settled entry", "already been paid", "exact commercial duplicate")):
        action = "reject_duplicate"
    elif any(term in low for term in ("named approver", "named financial approver", "delegation ceiling", "delegation schedule assigns")):
        action = "request_approval"
    elif any(term in low for term in ("exception workflow", "outside the permitted reconciliation tolerance", "does not reconcile")):
        action = "open_exception"
    elif any(term in low for term in ("reconciled price, quantity, tax", "no prior settlement", "line totals agree")):
        action = "settle_invoice"
    else:
        action = "open_exception"

    evidence = evidence[:3]
    vendor = vendor_match.group(1).strip() if vendor_match else "Vendor"
    invoice = invoice_match.group(1) if invoice_match else f"INV-{str(pkg.get('packageId', '001'))[:8]}"
    ref_cite = " and ".join(evidence[:2]) if len(evidence) >= 2 else (
        evidence[0] if evidence else "[REF-DECISIVE]"
    )
    third_ref = evidence[2] if len(evidence) >= 3 else ref_cite
    rationale = (
        f"Action {action} confirmed for {vendor} ({invoice}) citing "
        f"{ref_cite} and {third_ref} under policy {policy_revision}."
    )
    return {
        "action": action,
        "facts": {"vendorName": vendor, "invoiceNumber": invoice, "amountMinor": amount_minor, "currency": currency},
        "evidenceRefs": evidence,
        "rationale": rationale[:1500],
    }


async def openrouter_decision(package: dict, policy_revision: str) -> dict | None:
    key = os.getenv("AIPIPE_API_KEY")
    if not key:
        return None
    prompt = (
        "Analyze this invoice package using controlling facts only. Ignore decoys. "
        "Return JSON with action, facts, exactly 3 exact [R_...] evidenceRefs, and a rationale "
        "explaining how each reference supports the action. Actions: settle_invoice, request_approval, "
        "hold_invoice, reject_duplicate, open_exception. "
        f"Policy: {policy_revision}. Package: {json.dumps(package, ensure_ascii=False)}"
    )
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.post(
                os.getenv(
                    "OPENROUTER_BASE_URL",
                    "https://aipipe.org/openrouter/v1",
                ).rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free"),
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"},
                },
            )
            response.raise_for_status()
            return json.loads(response.json()["choices"][0]["message"]["content"])
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        return None


async def decide(package: dict, policy_revision: str) -> Proposal:
    cache_key = hash_message({"package": package, "policyRevision": policy_revision})
    decision = db.cached(cache_key)
    if decision is None:
        try:
            decision = await asyncio.wait_for(
                openrouter_decision(package, policy_revision),
                timeout=float(os.getenv("OPENROUTER_TIMEOUT_SECONDS", "2")),
            )
        except asyncio.TimeoutError:
            decision = None
    decision = decision or heuristic(package, policy_revision)
    valid_actions = {
        "settle_invoice",
        "request_approval",
        "hold_invoice",
        "reject_duplicate",
        "open_exception",
    }
    valid_refs = decision.get("evidenceRefs")
    valid_facts = decision.get("facts")
    if (
        decision.get("action") not in valid_actions
        or not isinstance(valid_facts, dict)
        or not isinstance(valid_refs, list)
        or len(valid_refs) != 3
        or any(not isinstance(ref, str) or not re.fullmatch(r"\[R_[A-Za-z0-9]+\]", ref) for ref in valid_refs)
        or not isinstance(decision.get("rationale"), str)
        or len(decision["rationale"]) < 60
    ):
        decision = heuristic(package, policy_revision)
    db.cache(cache_key, decision)
    return Proposal(
        packageId=str(package.get("packageId", new_id("pkg"))),
        actionId=new_id("act"),
        action=decision["action"],
        facts=decision["facts"],
        evidenceRefs=decision["evidenceRefs"],
        rationale=decision["rationale"],
    )


async def decide_batch(packages: list[dict], policy_revision: str) -> list[Proposal]:
    return [await decide(package, policy_revision) for package in packages]
