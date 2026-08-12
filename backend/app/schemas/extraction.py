from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

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
