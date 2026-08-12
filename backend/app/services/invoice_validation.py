import re
from datetime import date
from decimal import Decimal

from app.schemas.extraction import CanonicalInvoice, ValidationIssue

_GSTIN_PATTERN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_PAN_PATTERN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_AMOUNT_TOLERANCE = Decimal("0.02")
_GSTIN_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _decimal(value: str | None) -> Decimal:
    return Decimal(value) if value is not None else Decimal(0)


def _different(left: Decimal, right: Decimal) -> bool:
    return abs(left - right) > _AMOUNT_TOLERANCE


def _issue(
    code: str,
    field_path: str,
    message: str,
    *,
    severity: str = "warning",
) -> ValidationIssue:
    return ValidationIssue(
        severity=severity,
        code=code,
        field_path=field_path,
        message=message,
    )


def _valid_gstin_checksum(gstin: str) -> bool:
    factor = 1
    total = 0
    for character in gstin[:14]:
        product = _GSTIN_ALPHABET.index(character) * factor
        total += product // 36 + product % 36
        factor = 1 if factor == 2 else 2
    check_code = (36 - total % 36) % 36
    return gstin[14] == _GSTIN_ALPHABET[check_code]


def _validate_party(invoice: CanonicalInvoice, party_name: str) -> list[ValidationIssue]:
    party = getattr(invoice, party_name)
    issues: list[ValidationIssue] = []
    if not party.name:
        issues.append(
            _issue(
                "MISSING_PARTY_NAME",
                f"/{party_name}/name",
                f"{party_name.title()} name was not extracted",
            )
        )
    if party.gstin:
        gstin = party.gstin.upper()
        if not _GSTIN_PATTERN.fullmatch(gstin):
            issues.append(
                _issue(
                    "INVALID_GSTIN_FORMAT",
                    f"/{party_name}/gstin",
                    f"{party_name.title()} GSTIN format is invalid",
                )
            )
        elif not _valid_gstin_checksum(gstin):
            issues.append(
                _issue(
                    "INVALID_GSTIN_CHECKSUM",
                    f"/{party_name}/gstin",
                    f"{party_name.title()} GSTIN checksum is invalid",
                )
            )
    if party.pan and not _PAN_PATTERN.fullmatch(party.pan.upper()):
        issues.append(
            _issue(
                "INVALID_PAN_FORMAT",
                f"/{party_name}/pan",
                f"{party_name.title()} PAN format is invalid",
            )
        )
    return issues


def _validate_required_fields(invoice: CanonicalInvoice) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    required = (
        (invoice.invoice_number, "/invoice_number", "MISSING_INVOICE_NUMBER"),
        (invoice.invoice_date, "/invoice_date", "MISSING_INVOICE_DATE"),
        (invoice.totals.grand_total, "/totals/grand_total", "MISSING_GRAND_TOTAL"),
    )
    for value, field_path, code in required:
        if value is None or not str(value).strip():
            issues.append(_issue(code, field_path, "Required invoice value was not extracted"))

    if invoice.invoice_date:
        try:
            date.fromisoformat(invoice.invoice_date)
        except ValueError:
            issues.append(
                _issue(
                    "INVALID_INVOICE_DATE",
                    "/invoice_date",
                    "Invoice date must use ISO 8601 format YYYY-MM-DD",
                )
            )
    if invoice.due_date:
        try:
            date.fromisoformat(invoice.due_date)
        except ValueError:
            issues.append(
                _issue(
                    "INVALID_DUE_DATE",
                    "/due_date",
                    "Due date must use ISO 8601 format YYYY-MM-DD",
                )
            )
    if invoice.currency and invoice.currency.upper() != "INR":
        issues.append(
            _issue(
                "NON_INR_CURRENCY",
                "/currency",
                "Invoice currency is not INR and requires review",
            )
        )
    return issues


def _validate_line_items(invoice: CanonicalInvoice) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for index, item in enumerate(invoice.line_items):
        path = f"/line_items/{index}"
        if not item.description:
            issues.append(
                _issue(
                    "MISSING_LINE_DESCRIPTION",
                    f"{path}/description",
                    "Line-item description was not extracted",
                )
            )
        if (
            item.quantity is not None
            and item.unit_price is not None
            and item.taxable_value is not None
        ):
            expected_taxable = _decimal(item.quantity) * _decimal(item.unit_price) - _decimal(
                item.discount
            )
            if _different(_decimal(item.taxable_value), expected_taxable):
                issues.append(
                    _issue(
                        "LINE_TAXABLE_VALUE_MISMATCH",
                        f"{path}/taxable_value",
                        "Line taxable value does not match quantity, unit price, and discount",
                    )
                )

        cgst = _decimal(item.tax_amounts.cgst)
        sgst = _decimal(item.tax_amounts.sgst)
        igst = _decimal(item.tax_amounts.igst)
        if (cgst or sgst) and igst:
            issues.append(
                _issue(
                    "MIXED_GST_COMPONENTS",
                    f"{path}/tax_amounts",
                    "Line contains both IGST and CGST/SGST",
                )
            )
        if (cgst or sgst) and _different(cgst, sgst):
            issues.append(
                _issue(
                    "CGST_SGST_MISMATCH",
                    f"{path}/tax_amounts",
                    "Line CGST and SGST amounts are not equal",
                )
            )
        if item.taxable_value is not None and item.gst_rate is not None:
            expected_tax = _decimal(item.taxable_value) * _decimal(item.gst_rate) / Decimal(100)
            actual_tax = cgst + sgst + igst
            if _different(actual_tax, expected_tax):
                issues.append(
                    _issue(
                        "LINE_GST_AMOUNT_MISMATCH",
                        f"{path}/tax_amounts",
                        "Line GST amount does not match taxable value and GST rate",
                    )
                )
    return issues


def _validate_totals(invoice: CanonicalInvoice) -> list[ValidationIssue]:
    if not invoice.line_items:
        return [
            _issue(
                "NO_LINE_ITEMS",
                "/line_items",
                "No invoice line items were extracted",
            )
        ]

    issues: list[ValidationIssue] = []
    totals = invoice.totals
    line_taxable = sum((_decimal(item.taxable_value) for item in invoice.line_items), Decimal(0))
    if totals.taxable_amount is not None and _different(
        _decimal(totals.taxable_amount), line_taxable
    ):
        issues.append(
            _issue(
                "TAXABLE_TOTAL_MISMATCH",
                "/totals/taxable_amount",
                "Taxable total does not match the line-item sum",
            )
        )

    component_fields = (
        ("cgst", "cgst_amount"),
        ("sgst", "sgst_amount"),
        ("igst", "igst_amount"),
        ("cess", "cess_amount"),
    )
    for line_field, total_field in component_fields:
        line_sum = sum(
            (_decimal(getattr(item.tax_amounts, line_field)) for item in invoice.line_items),
            Decimal(0),
        )
        total_value = getattr(totals, total_field)
        if total_value is not None and _different(_decimal(total_value), line_sum):
            issues.append(
                _issue(
                    "TAX_COMPONENT_TOTAL_MISMATCH",
                    f"/totals/{total_field}",
                    f"{line_field.upper()} total does not match the line-item sum",
                )
            )

    if totals.grand_total is not None and totals.taxable_amount is not None:
        expected_grand_total = sum(
            (
                _decimal(totals.taxable_amount),
                _decimal(totals.cgst_amount),
                _decimal(totals.sgst_amount),
                _decimal(totals.igst_amount),
                _decimal(totals.cess_amount),
                _decimal(totals.round_off),
            ),
            Decimal(0),
        )
        if _different(_decimal(totals.grand_total), expected_grand_total):
            issues.append(
                _issue(
                    "GRAND_TOTAL_MISMATCH",
                    "/totals/grand_total",
                    "Grand total does not match taxable and tax totals",
                )
            )
    return issues


def validate_invoice(invoice: CanonicalInvoice) -> list[ValidationIssue]:
    return [
        *_validate_required_fields(invoice),
        *_validate_party(invoice, "supplier"),
        *_validate_party(invoice, "buyer"),
        *_validate_line_items(invoice),
        *_validate_totals(invoice),
    ]
