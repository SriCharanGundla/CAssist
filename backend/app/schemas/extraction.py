from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CanonicalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceRegion(CanonicalModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class DocumentClassification(CanonicalModel):
    document_type: Literal[
        "tax_invoice",
        "invoice",
        "receipt",
        "credit_note",
        "debit_note",
        "cheque",
        "bank_statement",
        "other_financial_document",
    ]
    confidence: float = Field(ge=0, le=1)


class DraftField(CanonicalModel):
    label: str = Field(min_length=1, max_length=500)
    value: str = Field(min_length=1, max_length=20_000)
    page_number: int = Field(ge=1)
    region: EvidenceRegion | None = None


class DraftTable(CanonicalModel):
    title: str | None = Field(default=None, max_length=500)
    headers: list[str] = Field(min_length=1, max_length=100)
    rows: list[list[str]] = Field(min_length=1, max_length=5_000)
    page_numbers: list[int] = Field(min_length=1, max_length=100)

    @field_validator("headers")
    @classmethod
    def validate_headers(cls, headers: list[str]) -> list[str]:
        if any(not header.strip() or len(header) > 500 for header in headers):
            raise ValueError("Table headers must be non-blank strings of at most 500 characters")
        return headers

    @field_validator("rows")
    @classmethod
    def validate_rows(cls, rows: list[list[str]], info) -> list[list[str]]:
        header_count = len(info.data.get("headers", []))
        if any(len(row) != header_count for row in rows):
            raise ValueError("Every table row must match the header count")
        if any(len(cell) > 20_000 for row in rows for cell in row):
            raise ValueError("Table cells must contain at most 20000 characters")
        return rows


class DraftTextBlock(CanonicalModel):
    text: str = Field(min_length=1, max_length=50_000)
    page_number: int = Field(ge=1)
    region: EvidenceRegion | None = None


class GenericExtractionDraft(CanonicalModel):
    fields: list[DraftField] = Field(default_factory=list, max_length=2_000)
    tables: list[DraftTable] = Field(default_factory=list, max_length=100)
    text_blocks: list[DraftTextBlock] = Field(default_factory=list, max_length=500)
    quality_review_recommended: bool = False


class DraftPresentationSection(CanonicalModel):
    title: str = Field(min_length=1, max_length=200)
    target_paths: list[str] = Field(min_length=1, max_length=2_600)


class PresentationDraft(CanonicalModel):
    sections: list[DraftPresentationSection] = Field(default_factory=list, max_length=100)


class ExtractedField(DraftField):
    id: str = Field(pattern=r"^field-[0-9]{4}$")


class ExtractedTableCell(CanonicalModel):
    id: str = Field(pattern=r"^table-[0-9]{4}-r[0-9]{4}-c[0-9]{4}$")
    value: str = Field(max_length=20_000)


class ExtractedTableRow(CanonicalModel):
    id: str = Field(pattern=r"^table-[0-9]{4}-row-[0-9]{4}$")
    cells: list[ExtractedTableCell] = Field(min_length=1, max_length=100)


class ExtractedTable(CanonicalModel):
    id: str = Field(pattern=r"^table-[0-9]{4}$")
    title: str | None = Field(default=None, max_length=500)
    headers: list[str] = Field(min_length=1, max_length=100)
    rows: list[ExtractedTableRow] = Field(min_length=1, max_length=5_000)
    page_numbers: list[int] = Field(min_length=1, max_length=100)


class ExtractedTextBlock(DraftTextBlock):
    id: str = Field(pattern=r"^text-[0-9]{4}$")


class GenericDocumentExtraction(CanonicalModel):
    document_type: str = Field(min_length=1, max_length=100)
    fields: list[ExtractedField] = Field(default_factory=list, max_length=2_000)
    tables: list[ExtractedTable] = Field(default_factory=list, max_length=100)
    text_blocks: list[ExtractedTextBlock] = Field(default_factory=list, max_length=500)


class PresentationSection(CanonicalModel):
    id: str = Field(pattern=r"^section-[0-9]{4}$")
    title: str = Field(min_length=1, max_length=200)
    target_ids: list[str] = Field(min_length=1, max_length=2_600)


class DocumentPresentation(CanonicalModel):
    sections: list[PresentationSection] = Field(default_factory=list, max_length=101)


class DraftQualityIssue(CanonicalModel):
    target_path: str = Field(min_length=1, max_length=512)
    code: Literal[
        "possible_gibberish",
        "possible_ocr_error",
        "duplicate_observation",
        "illegible_text",
    ]
    message: str = Field(min_length=1, max_length=500)
    suggested_value: str | None = Field(default=None, max_length=20_000)


class QualityReviewDraft(CanonicalModel):
    issues: list[DraftQualityIssue] = Field(default_factory=list, max_length=200)


class QualityIssue(CanonicalModel):
    target_id: str = Field(min_length=1, max_length=100)
    code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=500)
    suggested_value: str | None = Field(default=None, max_length=20_000)
