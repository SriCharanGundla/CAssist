import json
from typing import Any
from uuid import UUID

from app.schemas.extraction import (
    DocumentPresentation,
    GenericDocumentExtraction,
    QualityIssue,
)

EXPORTER_VERSION = "tally-handoff-v3"


def build_reviewed_document(
    document: GenericDocumentExtraction,
    presentation: DocumentPresentation,
) -> dict[str, Any]:
    fields = {field.id: field for field in document.fields}
    tables = {table.id: table for table in document.tables}
    text_blocks = {block.id: block for block in document.text_blocks}
    excluded_target_ids = set(presentation.excluded_target_ids)
    sections: list[dict[str, Any]] = []
    for section in presentation.sections:
        section_fields = [
            {"label": fields[target_id].label, "value": fields[target_id].value}
            for target_id in section.target_ids
            if target_id in fields and target_id not in excluded_target_ids
        ]
        section_tables = []
        for target_id in section.target_ids:
            if target_id in excluded_target_ids:
                continue
            table = tables.get(target_id)
            if table is None:
                continue
            table_data: dict[str, Any] = {
                "headers": table.headers,
                "rows": [[cell.value for cell in row.cells] for row in table.rows],
            }
            if table.title:
                table_data["title"] = table.title
            section_tables.append(table_data)
        section_text = [
            text_blocks[target_id].text
            for target_id in section.target_ids
            if target_id in text_blocks and target_id not in excluded_target_ids
        ]
        if not section_fields and not section_tables and not section_text:
            continue
        section_data: dict[str, Any] = {"title": section.title}
        if section_fields:
            section_data["fields"] = section_fields
        if section_tables:
            section_data["tables"] = section_tables
        if section_text:
            section_data["text"] = section_text
        sections.append(section_data)
    return {"document_type": document.document_type, "sections": sections}


def build_tally_handoff(
    *,
    result_id: UUID,
    result_version: int,
    document: GenericDocumentExtraction,
    presentation: DocumentPresentation,
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
        "reviewed_document": build_reviewed_document(document, presentation),
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
        ],
    }
    if include_quality_issues:
        payload["quality_issues"] = [
            {
                "code": issue.code,
                "message": issue.message,
                **(
                    {"suggested_value": issue.suggested_value}
                    if issue.suggested_value is not None
                    else {}
                ),
            }
            for issue in quality_issues
        ]
    return payload


def serialize_tally_handoff(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
