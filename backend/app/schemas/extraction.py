from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

DecimalString = Annotated[
    str,
    StringConstraints(pattern=r"^-?(?:0|[1-9]\d*)(?:\.\d+)?$"),
]


class CanonicalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Party(CanonicalModel):
    name: str | None = None
    gstin: str | None = None
    pan: str | None = None
    address: str | None = None
    state_code: str | None = None


class TaxAmounts(CanonicalModel):
    cgst: DecimalString | None = None
    sgst: DecimalString | None = None
    igst: DecimalString | None = None
    cess: DecimalString | None = None


class InvoiceLineItem(CanonicalModel):
    description: str | None = None
    hsn_sac: str | None = None
    quantity: DecimalString | None = None
    unit: str | None = None
    unit_price: DecimalString | None = None
    discount: DecimalString | None = None
    taxable_value: DecimalString | None = None
    gst_rate: DecimalString | None = None
    tax_amounts: TaxAmounts = Field(default_factory=TaxAmounts)
    total: DecimalString | None = None
    source_pages: list[int] = Field(default_factory=list)


class InvoiceTotals(CanonicalModel):
    taxable_amount: DecimalString | None = None
    discount_amount: DecimalString | None = None
    cgst_amount: DecimalString | None = None
    sgst_amount: DecimalString | None = None
    igst_amount: DecimalString | None = None
    cess_amount: DecimalString | None = None
    round_off: DecimalString | None = None
    grand_total: DecimalString | None = None


class CanonicalInvoice(CanonicalModel):
    document_type: Literal["tax_invoice", "invoice"] = "invoice"
    invoice_number: str | None = None
    invoice_date: str | None = None
    due_date: str | None = None
    currency: str | None = None
    supplier: Party = Field(default_factory=Party)
    buyer: Party = Field(default_factory=Party)
    place_of_supply: str | None = None
    reverse_charge: bool | None = None
    line_items: list[InvoiceLineItem] = Field(default_factory=list)
    totals: InvoiceTotals = Field(default_factory=InvoiceTotals)
    notes: list[str] = Field(default_factory=list)


class ValidationIssue(CanonicalModel):
    severity: Literal["warning", "error"]
    code: str
    field_path: str
    message: str


class EvidenceRegion(CanonicalModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class FieldEvidence(CanonicalModel):
    field_path: str
    page_number: int = Field(ge=1)
    region: EvidenceRegion | None = None


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


class ExtractionCompletion(CanonicalModel):
    validated: Literal[True]


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
