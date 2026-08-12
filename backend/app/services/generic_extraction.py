import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from PIL import Image

from app.schemas.extraction import (
    DocumentPresentation,
    DraftQualityIssue,
    EvidenceRegion,
    ExtractedField,
    ExtractedTable,
    ExtractedTableCell,
    ExtractedTableRow,
    ExtractedTextBlock,
    GenericDocumentExtraction,
    GenericExtractionDraft,
    PresentationDraft,
    PresentationSection,
    QualityIssue,
    QualityReviewDraft,
)

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TARGET_PATH = re.compile(
    r"^/(?:fields/[0-9]+|text_blocks/[0-9]+|tables/[0-9]+/rows/[0-9]+/[0-9]+)$"
)
_PRESENTATION_TARGET_PATH = re.compile(r"^/(?:fields|tables|text_blocks)/[0-9]+$")


def _text_is_suspicious(value: str) -> bool:
    if "\ufffd" in value or _CONTROL_CHARACTERS.search(value):
        return True
    compact = "".join(character for character in value if not character.isspace())
    if len(compact) < 5:
        return False
    alphanumeric = sum(character.isalnum() for character in compact)
    return alphanumeric / len(compact) < 0.2


def deterministic_quality_candidates(
    draft: GenericExtractionDraft,
) -> list[DraftQualityIssue]:
    candidates: list[DraftQualityIssue] = []
    seen: set[tuple[str, str]] = set()
    for index, field in enumerate(draft.fields):
        identity = (field.label.casefold().strip(), field.value.casefold().strip())
        if identity in seen:
            candidates.append(
                DraftQualityIssue(
                    target_path=f"/fields/{index}",
                    code="duplicate_observation",
                    message="This label and value duplicate an earlier extracted field",
                )
            )
        seen.add(identity)
        if _text_is_suspicious(field.label) or _text_is_suspicious(field.value):
            candidates.append(
                DraftQualityIssue(
                    target_path=f"/fields/{index}",
                    code="possible_gibberish",
                    message="This extracted field contains suspicious characters",
                )
            )
    for index, block in enumerate(draft.text_blocks):
        if _text_is_suspicious(block.text):
            candidates.append(
                DraftQualityIssue(
                    target_path=f"/text_blocks/{index}",
                    code="possible_gibberish",
                    message="This extracted text contains suspicious characters",
                )
            )
    for table_index, table in enumerate(draft.tables):
        for row_index, row in enumerate(table.rows):
            for cell_index, value in enumerate(row):
                if _text_is_suspicious(value):
                    candidates.append(
                        DraftQualityIssue(
                            target_path=(
                                f"/tables/{table_index}/rows/{row_index}/{cell_index}"
                            ),
                            code="possible_gibberish",
                            message="This extracted table cell contains suspicious characters",
                        )
                    )
    return candidates[:200]


def needs_quality_review(draft: GenericExtractionDraft) -> bool:
    return draft.quality_review_recommended or bool(deterministic_quality_candidates(draft))


def deterministic_quality_issues(
    document: GenericDocumentExtraction,
) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    seen: set[tuple[str, str]] = set()
    for field in document.fields:
        identity = (field.label.casefold().strip(), field.value.casefold().strip())
        if identity in seen:
            issues.append(
                QualityIssue(
                    target_id=field.id,
                    code="duplicate_observation",
                    message="This label and value duplicate an earlier extracted field",
                )
            )
        seen.add(identity)
        if _text_is_suspicious(field.label) or _text_is_suspicious(field.value):
            issues.append(
                QualityIssue(
                    target_id=field.id,
                    code="possible_gibberish",
                    message="This extracted field contains suspicious characters",
                )
            )
    for block in document.text_blocks:
        if _text_is_suspicious(block.text):
            issues.append(
                QualityIssue(
                    target_id=block.id,
                    code="possible_gibberish",
                    message="This extracted text contains suspicious characters",
                )
            )
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                if _text_is_suspicious(cell.value):
                    issues.append(
                        QualityIssue(
                            target_id=cell.id,
                            code="possible_gibberish",
                            message="This extracted table cell contains suspicious characters",
                        )
                    )
    return issues[:200]


def _valid_region(region: EvidenceRegion | None, page_path: Path) -> EvidenceRegion | None:
    if region is None:
        return None
    with Image.open(page_path) as image:
        if region.x + region.width > image.width or region.y + region.height > image.height:
            return None
    return region


def _validate_page(page_number: int, page_paths: Sequence[Path]) -> None:
    if page_number > len(page_paths):
        raise ValueError("Extraction references a page outside the document")


def _target_id(document: GenericDocumentExtraction, target_path: str) -> str | None:
    if not _TARGET_PATH.fullmatch(target_path):
        return None
    parts = target_path.split("/")[1:]
    try:
        if parts[0] == "fields":
            return document.fields[int(parts[1])].id
        if parts[0] == "text_blocks":
            return document.text_blocks[int(parts[1])].id
        return document.tables[int(parts[1])].rows[int(parts[3])].cells[int(parts[4])].id
    except (IndexError, ValueError):
        return None


def finalize_extraction(
    draft: GenericExtractionDraft,
    document_type: str,
    page_paths: Sequence[Path],
    quality_review: QualityReviewDraft | None = None,
) -> tuple[GenericDocumentExtraction, list[QualityIssue]]:
    fields: list[ExtractedField] = []
    for index, field in enumerate(draft.fields, start=1):
        _validate_page(field.page_number, page_paths)
        field_data = field.model_dump()
        field_data["region"] = _valid_region(field.region, page_paths[field.page_number - 1])
        fields.append(ExtractedField(id=f"field-{index:04d}", **field_data))

    tables: list[ExtractedTable] = []
    for table_index, table in enumerate(draft.tables, start=1):
        for page_number in table.page_numbers:
            _validate_page(page_number, page_paths)
        table_id = f"table-{table_index:04d}"
        rows = [
            ExtractedTableRow(
                id=f"{table_id}-row-{row_index:04d}",
                cells=[
                    ExtractedTableCell(
                        id=f"{table_id}-r{row_index:04d}-c{cell_index:04d}",
                        value=value,
                    )
                    for cell_index, value in enumerate(row, start=1)
                ],
            )
            for row_index, row in enumerate(table.rows, start=1)
        ]
        tables.append(
            ExtractedTable(
                id=table_id,
                title=table.title,
                headers=table.headers,
                rows=rows,
                page_numbers=sorted(set(table.page_numbers)),
            )
        )

    text_blocks: list[ExtractedTextBlock] = []
    for index, block in enumerate(draft.text_blocks, start=1):
        _validate_page(block.page_number, page_paths)
        block_data = block.model_dump()
        block_data["region"] = _valid_region(block.region, page_paths[block.page_number - 1])
        text_blocks.append(ExtractedTextBlock(id=f"text-{index:04d}", **block_data))

    document = GenericDocumentExtraction(
        document_type=document_type,
        fields=fields,
        tables=tables,
        text_blocks=text_blocks,
    )
    review_issues = quality_review.issues if quality_review else []
    issues: list[QualityIssue] = []
    seen_targets: set[tuple[str, str]] = set()
    for issue in [*deterministic_quality_candidates(draft), *review_issues]:
        target_id = _target_id(document, issue.target_path)
        identity = (target_id or "", issue.code)
        if target_id is None or identity in seen_targets:
            continue
        seen_targets.add(identity)
        issues.append(
            QualityIssue(
                target_id=target_id,
                code=issue.code,
                message=issue.message,
                suggested_value=issue.suggested_value,
            )
        )
    return document, issues[:200]


def _presentation_target_id(
    document: GenericDocumentExtraction,
    target_path: str,
) -> str | None:
    if not _PRESENTATION_TARGET_PATH.fullmatch(target_path):
        return None
    collection_name, raw_index = target_path.split("/")[1:]
    try:
        collection = getattr(document, collection_name)
        return collection[int(raw_index)].id
    except (AttributeError, IndexError, ValueError):
        return None


def _all_presentation_target_ids(document: GenericDocumentExtraction) -> list[str]:
    observations: list[tuple[int, int, int, str]] = []
    observations.extend(
        (field.page_number, 0, index, field.id)
        for index, field in enumerate(document.fields)
    )
    observations.extend(
        (min(table.page_numbers), 1, index, table.id)
        for index, table in enumerate(document.tables)
    )
    observations.extend(
        (block.page_number, 2, index, block.id)
        for index, block in enumerate(document.text_blocks)
    )
    return [target_id for *_, target_id in sorted(observations)]


def finalize_presentation(
    document: GenericDocumentExtraction,
    draft: PresentationDraft | None,
) -> DocumentPresentation:
    sections: list[PresentationSection] = []
    assigned: set[str] = set()
    for section in draft.sections if draft else []:
        target_ids: list[str] = []
        for target_path in section.target_paths:
            target_id = _presentation_target_id(document, target_path)
            if target_id is None or target_id in assigned:
                continue
            assigned.add(target_id)
            target_ids.append(target_id)
        if target_ids:
            sections.append(
                PresentationSection(
                    id=f"section-{len(sections) + 1:04d}",
                    title=section.title.strip() or "Document section",
                    target_ids=target_ids,
                )
            )

    unassigned = [
        target_id
        for target_id in _all_presentation_target_ids(document)
        if target_id not in assigned
    ]
    if unassigned:
        sections.append(
            PresentationSection(
                id=f"section-{len(sections) + 1:04d}",
                title="Additional information" if sections else "Document details",
                target_ids=unassigned,
            )
        )
    return DocumentPresentation(sections=sections)


def coerce_stored_presentation(
    data: dict[str, Any] | None,
    document: GenericDocumentExtraction,
) -> DocumentPresentation:
    if data:
        try:
            presentation = DocumentPresentation.model_validate(data)
        except ValueError:
            presentation = DocumentPresentation()
        known_ids = set(_all_presentation_target_ids(document))
        sections: list[PresentationSection] = []
        assigned: set[str] = set()
        for section in presentation.sections:
            target_ids = [
                target_id
                for target_id in section.target_ids
                if target_id in known_ids and target_id not in assigned
            ]
            assigned.update(target_ids)
            if target_ids:
                sections.append(
                    PresentationSection(
                        id=f"section-{len(sections) + 1:04d}",
                        title=section.title,
                        target_ids=target_ids,
                    )
                )
        missing = [target_id for target_id in known_ids if target_id not in assigned]
        if missing:
            ordered_missing = [
                target_id
                for target_id in _all_presentation_target_ids(document)
                if target_id in missing
            ]
            sections.append(
                PresentationSection(
                    id=f"section-{len(sections) + 1:04d}",
                    title="Additional information" if sections else "Document details",
                    target_ids=ordered_missing,
                )
            )
        return DocumentPresentation(sections=sections)
    return finalize_presentation(document, None)


def target_value_path(document: GenericDocumentExtraction, target_id: str) -> str:
    for index, field in enumerate(document.fields):
        if field.id == target_id:
            return f"/fields/{index}/value"
    for table_index, table in enumerate(document.tables):
        for row_index, row in enumerate(table.rows):
            for cell_index, cell in enumerate(row.cells):
                if cell.id == target_id:
                    return (
                        f"/tables/{table_index}/rows/{row_index}/cells/{cell_index}/value"
                    )
    for index, block in enumerate(document.text_blocks):
        if block.id == target_id:
            return f"/text_blocks/{index}/text"
    raise ValueError("Correction target does not exist")


def _display_label(path: list[str]) -> str:
    return " ".join(part.replace("_", " ").title() for part in path)


def coerce_stored_extraction(
    data: dict[str, Any],
    document_type: str,
) -> tuple[GenericDocumentExtraction, dict[str, str]]:
    """Read generic results and project legacy invoice results without discarding old data."""
    if "fields" in data and "tables" in data and "text_blocks" in data:
        document = GenericDocumentExtraction.model_validate(data)
        return document, {
            target_id: target_value_path(document, target_id)
            for target_id in [
                *(field.id for field in document.fields),
                *(
                    cell.id
                    for table in document.tables
                    for row in table.rows
                    for cell in row.cells
                ),
                *(block.id for block in document.text_blocks),
            ]
        }

    fields: list[ExtractedField] = []
    target_paths: dict[str, str] = {}

    def add_field(label_parts: list[str], value: Any, path: str) -> None:
        if value is None or value == "" or isinstance(value, (dict, list)):
            return
        field_id = f"field-{len(fields) + 1:04d}"
        fields.append(
            ExtractedField(
                id=field_id,
                label=_display_label(label_parts),
                value="Yes" if value is True else "No" if value is False else str(value),
                page_number=1,
            )
        )
        target_paths[field_id] = path

    for key, value in data.items():
        if key in {"document_type", "line_items", "notes"}:
            continue
        if isinstance(value, dict):
            for nested_key, nested_value in value.items():
                add_field([key, nested_key], nested_value, f"/{key}/{nested_key}")
        else:
            add_field([key], value, f"/{key}")

    tables: list[ExtractedTable] = []
    line_items = data.get("line_items")
    if isinstance(line_items, list) and line_items:
        headers: list[str] = []
        keys: list[tuple[str, str | None]] = []
        for item in line_items:
            if not isinstance(item, dict):
                continue
            for key, value in item.items():
                if key == "source_pages":
                    continue
                if isinstance(value, dict):
                    for nested_key in value:
                        identity = (key, nested_key)
                        if identity not in keys:
                            keys.append(identity)
                            headers.append(_display_label([key, nested_key]))
                elif (key, None) not in keys:
                    keys.append((key, None))
                    headers.append(_display_label([key]))
        rows: list[ExtractedTableRow] = []
        for row_index, item in enumerate(line_items, start=1):
            cells: list[ExtractedTableCell] = []
            for cell_index, (key, nested_key) in enumerate(keys, start=1):
                raw_value = item.get(key) if isinstance(item, dict) else None
                value = (
                    raw_value.get(nested_key)
                    if nested_key and isinstance(raw_value, dict)
                    else raw_value
                )
                cell_id = f"table-0001-r{row_index:04d}-c{cell_index:04d}"
                cells.append(
                    ExtractedTableCell(
                        id=cell_id,
                        value="" if value is None else str(value),
                    )
                )
                suffix = f"/{key}/{nested_key}" if nested_key else f"/{key}"
                target_paths[cell_id] = f"/line_items/{row_index - 1}{suffix}"
            rows.append(ExtractedTableRow(id=f"table-0001-row-{row_index:04d}", cells=cells))
        if headers and rows:
            pages = sorted(
                {
                    page
                    for item in line_items
                    if isinstance(item, dict)
                    for page in item.get("source_pages", [])
                    if isinstance(page, int) and page > 0
                }
            ) or [1]
            tables.append(
                ExtractedTable(
                    id="table-0001",
                    title="Line Items",
                    headers=headers,
                    rows=rows,
                    page_numbers=pages,
                )
            )

    text_blocks: list[ExtractedTextBlock] = []
    notes = data.get("notes")
    if isinstance(notes, list):
        for note_index, note in enumerate(notes, start=1):
            if not isinstance(note, str) or not note:
                continue
            block_id = f"text-{note_index:04d}"
            text_blocks.append(ExtractedTextBlock(id=block_id, text=note, page_number=1))
            target_paths[block_id] = f"/notes/{note_index - 1}"

    return (
        GenericDocumentExtraction(
            document_type=document_type,
            fields=fields,
            tables=tables,
            text_blocks=text_blocks,
        ),
        target_paths,
    )
