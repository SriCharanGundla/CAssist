import json
from typing import Any
from uuid import UUID

from app.schemas.extraction import GenericDocumentExtraction, QualityIssue

EXPORTER_VERSION = "tally-handoff-v2"


def build_tally_handoff(
    *,
    result_id: UUID,
    result_version: int,
    document: GenericDocumentExtraction,
    quality_issues: list[QualityIssue],
    include_quality_issues: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "format": "cassist.tally_handoff",
        "schema_version": EXPORTER_VERSION,
        "tally_compatibility": {
            "target": "TallyPrime 7.0+",
            "native_import_ready": False,
            "reason_code": "HUMAN_FIELD_AND_MASTER_MAPPING_REQUIRED",
        },
        "source": {
            "result_id": str(result_id),
            "result_version": result_version,
            "document_type": document.document_type,
            "review_status": "approved",
        },
        "reviewed_extraction": document.model_dump(mode="json"),
        "required_mappings": [
            {
                "code": "TARGET_COMPANY",
                "description": "Select the Tally company receiving this document",
            },
            {
                "code": "VOUCHER_TYPE",
                "description": "Choose the target voucher type",
            },
            {
                "code": "DOCUMENT_FIELDS",
                "description": "Map reviewed source labels to Tally voucher fields",
            },
            {
                "code": "LEDGER_AND_ITEM_MASTERS",
                "description": "Map applicable values and table rows to existing masters",
            },
        ],
    }
    if include_quality_issues:
        payload["quality_issues"] = [issue.model_dump(mode="json") for issue in quality_issues]
    return payload


def serialize_tally_handoff(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
