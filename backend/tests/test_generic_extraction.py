from pathlib import Path

from PIL import Image

from app.schemas.extraction import (
    DraftField,
    DraftTable,
    EvidenceRegion,
    GenericExtractionDraft,
    QualityReviewDraft,
)
from app.services.generic_extraction import finalize_extraction, target_value_path


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
