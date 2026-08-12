import pytest
from pydantic import ValidationError

from app.schemas.extraction import (
    CanonicalInvoice,
    InvoiceLineItem,
    InvoiceTotals,
    Party,
    TaxAmounts,
)
from app.services.invoice_validation import validate_invoice


def _valid_invoice() -> CanonicalInvoice:
    return CanonicalInvoice(
        invoice_number="INV-1042",
        invoice_date="2026-08-12",
        supplier=Party(name="Supplier LLP", gstin="27AAPFU0939F1ZV"),
        buyer=Party(name="Buyer Pvt Ltd"),
        line_items=[
            InvoiceLineItem(
                description="Accounting services",
                hsn_sac="9982",
                quantity="2",
                unit_price="500.00",
                discount="0",
                taxable_value="1000.00",
                gst_rate="18",
                tax_amounts=TaxAmounts(cgst="90.00", sgst="90.00"),
                total="1180.00",
                source_pages=[1],
            )
        ],
        totals=InvoiceTotals(
            taxable_amount="1000.00",
            cgst_amount="90.00",
            sgst_amount="90.00",
            igst_amount="0",
            cess_amount="0",
            round_off="0",
            grand_total="1180.00",
        ),
    )


def test_canonical_invoice_preserves_every_decimal_as_a_json_string() -> None:
    payload = _valid_invoice().model_dump(mode="json")

    assert payload["line_items"][0]["quantity"] == "2"
    assert payload["line_items"][0]["unit_price"] == "500.00"
    assert payload["totals"]["grand_total"] == "1180.00"
    assert validate_invoice(_valid_invoice()) == []


@pytest.mark.parametrize("invalid_value", [1180.0, "1,180.00", "₹1180", "01.00", "NaN"])
def test_canonical_invoice_rejects_non_decimal_string_amounts(invalid_value: object) -> None:
    with pytest.raises(ValidationError):
        InvoiceTotals(grand_total=invalid_value)


def test_validator_reports_arithmetic_tax_identity_and_required_field_warnings() -> None:
    invoice = _valid_invoice().model_copy(deep=True)
    invoice.invoice_number = None
    invoice.invoice_date = "12/08/2026"
    invoice.supplier.gstin = "27AAPFU0939F1ZA"
    invoice.line_items[0].taxable_value = "900.00"
    invoice.line_items[0].tax_amounts.igst = "162.00"
    invoice.totals.taxable_amount = "800.00"
    invoice.totals.grand_total = "999.00"

    issues = validate_invoice(invoice)
    codes = {issue.code for issue in issues}

    assert {
        "MISSING_INVOICE_NUMBER",
        "INVALID_INVOICE_DATE",
        "INVALID_GSTIN_CHECKSUM",
        "LINE_TAXABLE_VALUE_MISMATCH",
        "MIXED_GST_COMPONENTS",
        "TAXABLE_TOTAL_MISMATCH",
        "GRAND_TOTAL_MISMATCH",
    } <= codes
    assert all(issue.severity == "warning" for issue in issues)
    assert all(issue.field_path.startswith("/") for issue in issues)


def test_validator_uses_tolerance_for_normal_currency_rounding() -> None:
    invoice = _valid_invoice().model_copy(deep=True)
    invoice.totals.grand_total = "1180.01"

    assert validate_invoice(invoice) == []
