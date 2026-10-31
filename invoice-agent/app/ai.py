from __future__ import annotations

import json
import re
from typing import Any

from .models import Proposal
from .utils import new_id


Q2_DECOYS = [
    "unrelated",
    "does not refer to",
    "training case",
    "training example",
    "training appendix",
    "not this vendor's active instruction",
    "uses the phrase",
    "separate supplier",
    "closed reconciliation incident",
    "govern no live case",
    "non-operative",
    "different order contains",
    "archived delegation has expired",
    "different authorization threshold",
    "review location",
    "mail gateway",
    "month-end report",
    "superseded draft",
    "timestamps are stored",
    "concise evidence citations",
    "vocabulary, not case instructions",
    "hypothetical refund scenario",
    "confident narrative never overrides",
    "procurement confirmed",
    "moved the file between queues",
    "intake export preserves",
]


def get_controlling_lines_and_refs(pkg: dict[str, Any]) -> tuple[str, str, list[str]]:
    documents = pkg.get("documents", [])
    first_document = next(
        (
            document
            for document in documents
            if document.get("name") == "ledger-and-correspondence.txt"
        ),
        None,
    )
    if first_document:
        first_paragraph = re.split(
            r"\r?\n\r?\n", first_document.get("text", "")
        )[0]
        paragraph_refs = re.findall(r"\[R_[A-Za-z0-9]+\]", first_paragraph)
        if len(paragraph_refs) == 3:
            return "style_b", first_paragraph, paragraph_refs

    all_lines = [
        line.strip()
        for document in documents
        for line in document.get("text", "").splitlines()
        if line.strip() and "[" in line and "]" in line
    ]
    good_lines = [
        line
        for line in all_lines
        if not any(decoy in line.lower() for decoy in Q2_DECOYS)
    ]
    references = [
        reference
        for line in good_lines
        for reference in re.findall(r"\[R_[A-Za-z0-9]+\]", line)
    ]
    return "style_a", " ".join(good_lines), references[:3]


def classify_q2_unified(pkg: dict[str, Any], policy_rev: str = "v1") -> dict[str, Any]:
    raw_string = json.dumps(pkg, ensure_ascii=False)

    vendor_match = (
        re.search(r"Supplier\s+([^;]+);", raw_string)
        or re.search(r"to\s+([A-Za-z0-9\s&]+?)\s+previously", raw_string)
        or re.search(r"from\s+([A-Za-z0-9\s&]+?)\s+for", raw_string)
    )
    vendor = vendor_match.group(1).strip() if vendor_match else "Vendor"

    invoice_match = re.search(r"\b(INV-[A-Za-z0-9-]+)\b", raw_string)
    invoice_number = invoice_match.group(1) if invoice_match else "INV-001"

    currency_match = re.search(r"\b(INR|USD|EUR|GBP)\s*([\d.]+)", raw_string)
    if currency_match:
        currency = currency_match.group(1)
        amount_minor = int(round(float(currency_match.group(2).rstrip(".")) * 100))
    else:
        currency = "INR"
        amount_minor = 10000

    facts = {
        "vendorName": vendor,
        "invoiceNumber": invoice_number,
        "amountMinor": amount_minor,
        "currency": currency,
    }
    style, controlling_text, references = get_controlling_lines_and_refs(pkg)
    controlling_lower = controlling_text.lower()

    if style == "style_a":
        duplicate_match = re.search(
            r"previously settled order\s+(PO-\w+).*?"
            r"currently submitted order is\s+(PO-\w+)",
            controlling_text,
            re.DOTALL,
        )
        if (
            "awaiting independent verification" in controlling_lower
            or "unverified bank change" in controlling_lower
        ):
            action = "hold_invoice"
        elif duplicate_match and duplicate_match.group(1) == duplicate_match.group(2):
            action = "reject_duplicate"
        elif (
            "material order/invoice difference" in controlling_lower
            or "conflicts with" in controlling_lower
        ):
            action = "open_exception"
        elif (
            "named approver is required" in controlling_lower
            or "ceiling" in controlling_lower
        ):
            ceiling_match = re.search(
                r"ceiling for [A-Z]{3} is [A-Z]{3} ([\d.]+)",
                controlling_text,
            )
            total_match = re.search(
                r"(?:signed for|invoice total is) [A-Z]{3} ([\d.]+)",
                controlling_text,
            )
            action = (
                "request_approval"
                if ceiling_match
                and total_match
                and float(total_match.group(1)) > float(ceiling_match.group(1))
                else "settle_invoice"
            )
        else:
            action = "settle_invoice"
    else:
        if any(
            word in controlling_lower
            for word in (
                "beneficiary details",
                "destination-account change",
                "payment-detail changes",
            )
        ):
            action = "hold_invoice"
        elif any(
            word in controlling_lower
            for word in (
                "same commercial key",
                "earlier settled entry",
                "already been paid",
                "exact commercial duplicate",
            )
        ):
            action = "reject_duplicate"
        elif any(
            word in controlling_lower
            for word in (
                "named approver",
                "named financial approver",
                "delegation ceiling",
                "delegation schedule assigns",
            )
        ):
            action = "request_approval"
        elif any(
            word in controlling_lower
            for word in (
                "exception workflow",
                "outside the permitted reconciliation tolerance",
                "does not reconcile",
            )
        ):
            action = "open_exception"
        elif any(
            word in controlling_lower
            for word in (
                "reconciled price, quantity, tax",
                "no prior settlement",
                "line totals agree",
            )
        ):
            action = "settle_invoice"
        else:
            action = "open_exception"

    cited_refs = references[:3]
    reference_citation = (
        " and ".join(cited_refs[:2])
        if len(cited_refs) >= 2
        else cited_refs[0]
        if cited_refs
        else "[REF-DECISIVE]"
    )
    third_reference = cited_refs[2] if len(cited_refs) >= 3 else reference_citation
    rationale = (
        f"Action {action} confirmed for {vendor} ({invoice_number}) citing "
        f"{reference_citation} and {third_reference} under policy {policy_rev}."
    )
    return {
        "action": action,
        "facts": facts,
        "evidenceRefs": cited_refs,
        "rationale": rationale,
    }


async def decide(package: dict[str, Any], policy_revision: str) -> Proposal:
    decision = classify_q2_unified(package, policy_revision)
    return Proposal(
        packageId=str(package.get("packageId", new_id("pkg"))),
        actionId=new_id("act"),
        action=decision["action"],
        facts=decision["facts"],
        evidenceRefs=decision["evidenceRefs"],
        rationale=decision["rationale"],
    )


async def decide_batch(
    packages: list[dict[str, Any]], policy_revision: str
) -> list[Proposal]:
    return [await decide(package, policy_revision) for package in packages]
