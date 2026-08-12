from pathlib import Path

from PIL import Image

from app.schemas.extraction import (
    DocumentPresentation,
    DraftField,
    DraftTable,
    DraftTextBlock,
    EvidenceRegion,
    GenericExtractionDraft,
    PresentationDraft,
    QualityReviewDraft,
)
from app.services.generic_extraction import (
    coerce_stored_extraction,
    finalize_extraction,
    finalize_presentation,
    remove_non_accounting_boilerplate,
    target_value_path,
)


def test_finalization_assigns_stable_ids_and_preserves_source_labels(tmp_path: Path) -> None:
    page = tmp_path / "page.png"
    Image.new("RGB", (200, 100), "white").save(page)
    draft = GenericExtractionDraft(
        fields=[DraftField(label="Bill No.", value="A-102", page_number=1)],
        tables=[
            DraftTable(
                title="Particulars",
                headers=["Item Name", "Amt (₹)"],
                rows=[["Consulting", "5,000.00"]],
                page_numbers=[1],
            )
        ],
    )

    document, issues = finalize_extraction(draft, "invoice", [page])

    assert document.fields[0].id == "field-0001"
    assert document.fields[0].label == "Bill No."
    assert document.fields[0].value == "A-102"
    assert document.tables[0].headers == ["Item Name", "Amt (₹)"]
    assert document.tables[0].rows[0].cells[1].value == "5,000.00"
    assert target_value_path(document, "table-0001-r0001-c0002") == (
        "/tables/0/rows/0/cells/1/value"
    )
    assert issues == []


def test_finalization_normalizes_transport_artifacts(tmp_path: Path) -> None:
    page = tmp_path / "page.png"
    Image.new("RGB", (200, 100), "white").save(page)
    draft = GenericExtractionDraft(
        fields=[
            DraftField(
                label="Address",
                value="Pragati Advisory Services\\n21, MG Road",
                page_number=1,
            )
        ],
        tables=[
            DraftTable(
                title="Transactions 1\x9d7",
                headers=["Reference"],
                rows=[["INV-1"]],
                page_numbers=[1],
            )
        ],
    )

    document, _ = finalize_extraction(draft, "other_financial_document", [page])

    assert document.fields[0].value == "Pragati Advisory Services\n21, MG Road"
    assert document.tables[0].title == "Transactions 1-7"


def test_boilerplate_is_removed_without_removing_accounting_terms(tmp_path: Path) -> None:
    page = tmp_path / "page.png"
    Image.new("RGB", (200, 100), "white").save(page)
    draft = GenericExtractionDraft(
        text_blocks=[
            DraftTextBlock(
                text="CURRENT ACCOUNT STATEMENT\\nDakshin Cooperative Bank - continued",
                page_number=1,
            ),
            DraftTextBlock(
                text="SYNTHETIC TEST DOCUMENT - NOT FOR ACCOUNTING USE",
                page_number=1,
            ),
            DraftTextBlock(
                text="This is a computer-generated statement and does not require a signature.",
                page_number=1,
            ),
            DraftTextBlock(text="Payment due within 15 days", page_number=1),
        ]
    )
    document, issues = finalize_extraction(draft, "bank_statement", [page])
    presentation = DocumentPresentation(
        sections=[
            {
                "id": "section-0001",
                "title": "Other text",
                "target_ids": [block.id for block in document.text_blocks],
            }
        ]
    )

    document, presentation, issues = remove_non_accounting_boilerplate(
        document, presentation, issues
    )

    assert [block.text for block in document.text_blocks] == ["Payment due within 15 days"]
    assert presentation.sections[0].target_ids == ["text-0004"]
    assert issues == []


def test_stored_extraction_is_cleaned_for_review_and_export() -> None:
    stored = {
        "document_type": "bank_statement",
        "fields": [
            {
                "id": "field-0001",
                "label": "Address",
                "value": "Line one\\nLine two",
                "page_number": 1,
                "region": None,
            }
        ],
        "tables": [],
        "text_blocks": [
            {
                "id": "text-0001",
                "text": "SYNTHETIC TEST DOCUMENT - NOT FOR ACCOUNTING USE",
                "page_number": 1,
                "region": None,
            }
        ],
    }

    document, paths = coerce_stored_extraction(stored, "bank_statement")

    assert document.fields[0].value == "Line one\nLine two"
    assert document.text_blocks == []
    assert set(paths) == {"field-0001"}


def test_quality_suggestion_never_overwrites_extracted_value(tmp_path: Path) -> None:
    page = tmp_path / "page.png"
    Image.new("RGB", (200, 100), "white").save(page)
    draft = GenericExtractionDraft(
        fields=[DraftField(label="Bill No.", value="1NV-1O2", page_number=1)]
    )
    review = QualityReviewDraft(
        issues=[
            {
                "target_path": "/fields/0",
                "code": "possible_ocr_error",
                "message": "Possible character confusion",
                "suggested_value": "INV-102",
            }
        ]
    )

    document, issues = finalize_extraction(draft, "invoice", [page], review)

    assert document.fields[0].value == "1NV-1O2"
    assert issues[0].target_id == "field-0001"
    assert issues[0].suggested_value == "INV-102"


def test_out_of_bounds_optional_evidence_does_not_discard_extraction(
    tmp_path: Path,
) -> None:
    page = tmp_path / "page.png"
    Image.new("RGB", (200, 100), "white").save(page)
    draft = GenericExtractionDraft(
        fields=[
            DraftField(
                label="Invoice No.",
                value="INV-42",
                page_number=1,
                region=EvidenceRegion(x=150, y=60, width=100, height=60),
            )
        ]
    )

    document, issues = finalize_extraction(draft, "invoice", [page])

    assert document.fields[0].value == "INV-42"
    assert document.fields[0].region is None
    assert issues == []


def test_presentation_groups_existing_observations_without_dropping_data(
    tmp_path: Path,
) -> None:
    page = tmp_path / "page.png"
    Image.new("RGB", (200, 100), "white").save(page)
    draft = GenericExtractionDraft(
        fields=[
            DraftField(label="Invoice No.", value="INV-42", page_number=1),
            DraftField(label="Grand Total", value="118.00", page_number=1),
        ],
        tables=[
            DraftTable(
                title="Items",
                headers=["Description", "Amount"],
                rows=[["Services", "118.00"]],
                page_numbers=[1],
            )
        ],
    )
    document, _ = finalize_extraction(draft, "invoice", [page])
    presentation = finalize_presentation(
        document,
        PresentationDraft(
            sections=[
                {
                    "title": "Invoice details",
                    "target_paths": [
                        "/fields/0",
                        "/fields/0",
                        "/not-a-target/1",
                    ],
                }
            ]
        ),
    )

    assert presentation.sections[0].title == "Invoice details"
    assert presentation.sections[0].target_ids == ["field-0001"]
    assert presentation.sections[1].title == "Additional information"
    assert presentation.sections[1].target_ids == ["field-0002", "table-0001"]
