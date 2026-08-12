import json
from typing import Any
from uuid import UUID

from app.schemas.extraction import CanonicalInvoice, Party, ValidationIssue

EXPORTER_VERSION = "tally-handoff-v1"


def _party(party: Party) -> dict[str, Any]:
    return {
        "name": party.name,
        "gstin": party.gstin,
        "pan": party.pan,
        "address": party.address,
        "state_code": party.state_code,
    }


def build_tally_handoff(
    *,
    result_id: UUID,
    result_version: int,
    invoice: CanonicalInvoice,
    validation_issues: list[ValidationIssue],
    include_validation_warnings: bool,
) -> dict[str, Any]:
    invoice_date = invoice.invoice_date.replace("-", "") if invoice.invoice_date else None
    payload: dict[str, Any] = {
        "format": "cassist.tally_handoff",
        "schema_version": EXPORTER_VERSION,
        "tally_compatibility": {
            "target": "TallyPrime 7.0+",
            "native_import_ready": False,
            "reason_code": "COMPANY_AND_MASTER_MAPPING_REQUIRED",
        },
        "source": {
            "result_id": str(result_id),
            "result_version": result_version,
            "document_type": invoice.document_type,
            "review_status": "approved",
        },
        "voucher_draft": {
            "candidate_voucher_types": ["Purchase", "Sales"],
            "persisted_view": "Invoice Voucher View",
            "date": invoice_date,
            "voucher_number": invoice.invoice_number,
            "currency": invoice.currency,
            "supplier": _party(invoice.supplier),
            "buyer": _party(invoice.buyer),
            "place_of_supply": invoice.place_of_supply,
            "reverse_charge": invoice.reverse_charge,
            "line_items": [item.model_dump(mode="json") for item in invoice.line_items],
            "totals": invoice.totals.model_dump(mode="json"),
            "notes": invoice.notes,
        },
        "required_mappings": [
            {
                "code": "TARGET_COMPANY",
                "description": "Select the Tally company receiving this voucher",
            },
            {
                "code": "TRANSACTION_ROLE",
                "description": "Choose whether the document becomes a Purchase or Sales voucher",
            },
            {
                "code": "PARTY_LEDGER",
                "description": "Map the applicable party to an existing Tally ledger",
            },
            {
                "code": "ACCOUNTING_MODE",
                "description": "Choose accounting-ledger or inventory-item allocation",
            },
            {
                "code": "LINE_MASTERS",
                "description": "Map each line to existing ledger or stock-item and unit masters",
            },
        ],
    }
    if include_validation_warnings:
        payload["validation_warnings"] = [
            issue.model_dump(mode="json") for issue in validation_issues
        ]
    return payload


def serialize_tally_handoff(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
