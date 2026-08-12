"""Run one live, synthetic invoice extraction without persisting document data."""

from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw, ImageFont

from app.core.config import Settings
from app.services.invoice_validation import validate_invoice
from app.services.model_provider import (
    ProviderExtractionError,
    create_extraction_provider,
    resolve_model_selection,
)

FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ) if bold else FONT_CANDIDATES
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    raise RuntimeError("No supported smoke-test font was found")


def _synthetic_invoice(path: Path) -> None:
    image = Image.new("RGB", (1600, 2000), "white")
    draw = ImageDraw.Draw(image)
    title = _font(54, bold=True)
    heading = _font(34, bold=True)
    body = _font(30)
    small = _font(25)

    draw.text((80, 70), "TAX INVOICE — SYNTHETIC TEST DATA", fill="black", font=title)
    draw.text((80, 155), "Not a real accounting document", fill="#8B0000", font=heading)
    draw.line((80, 220, 1520, 220), fill="black", width=3)

    rows = (
        (280, "Invoice number", "SYN-2026-0042"),
        (335, "Invoice date", "12 August 2026"),
        (390, "Due date", "27 August 2026"),
        (470, "Supplier", "Synthetic Supplies Private Limited"),
        (525, "Supplier state code", "27 — Maharashtra"),
        (605, "Buyer", "Example Buyer LLP"),
        (660, "Buyer state code", "27 — Maharashtra"),
        (715, "Place of supply", "Maharashtra (27)"),
    )
    for y, label, value in rows:
        draw.text((80, y), f"{label}:", fill="black", font=heading)
        draw.text((470, y), value, fill="black", font=body)

    draw.line((80, 800, 1520, 800), fill="black", width=2)
    draw.text((80, 835), "Description", fill="black", font=heading)
    draw.text((690, 835), "Qty", fill="black", font=heading)
    draw.text((810, 835), "Rate", fill="black", font=heading)
    draw.text((1010, 835), "GST", fill="black", font=heading)
    draw.text((1220, 835), "Total", fill="black", font=heading)
    draw.line((80, 890, 1520, 890), fill="black", width=2)
    draw.text((80, 925), "Professional document review", fill="black", font=body)
    draw.text((690, 925), "2 NOS", fill="black", font=body)
    draw.text((810, 925), "500.00", fill="black", font=body)
    draw.text((1010, 925), "18%", fill="black", font=body)
    draw.text((1220, 925), "1180.00", fill="black", font=body)
    draw.line((80, 990, 1520, 990), fill="black", width=2)

    totals = (
        (1080, "Taxable amount", "1000.00"),
        (1135, "CGST 9%", "90.00"),
        (1190, "SGST 9%", "90.00"),
        (1245, "Grand total INR", "1180.00"),
    )
    for y, label, value in totals:
        draw.text((850, y), label, fill="black", font=heading)
        draw.text((1280, y), value, fill="black", font=body)

    draw.text(
        (80, 1450),
        "Reverse charge: No  |  Currency: INR  |  HSN/SAC: 9983",
        fill="black",
        font=small,
    )
    draw.text(
        (80, 1510),
        "This image is generated solely to verify the CAssist development extraction adapter.",
        fill="#444444",
        font=small,
    )
    image.save(path, format="PNG", optimize=True)


def main() -> int:
    settings = Settings()
    if settings.app_env == "production":
        print("REFUSED: live development smoke test cannot run in production")
        return 2
    if not settings.gemini_api_key:
        print("BLOCKED: GEMINI_API_KEY is not configured")
        return 2

    selection = resolve_model_selection(settings, provider_override="gemini")
    with TemporaryDirectory(prefix="cassist-live-model-") as directory:
        invoice_path = Path(directory) / "synthetic-invoice.png"
        _synthetic_invoice(invoice_path)
        try:
            extraction = create_extraction_provider(settings, selection).extract_invoice(
                [invoice_path]
            )
        except ProviderExtractionError:
            print("FAILED: live agentic extraction did not complete")
            return 1

    invoice = extraction.invoice
    expected = {
        "invoice_number": invoice.invoice_number == "SYN-2026-0042",
        "invoice_date": invoice.invoice_date == "2026-08-12",
        "supplier_name": invoice.supplier.name == "Synthetic Supplies Private Limited",
        "buyer_name": invoice.buyer.name == "Example Buyer LLP",
        "taxable_amount": invoice.totals.taxable_amount == "1000.00",
        "grand_total": invoice.totals.grand_total == "1180.00",
        "line_item": len(invoice.line_items) == 1,
    }
    failed_fields = [field for field, matches in expected.items() if not matches]
    if failed_fields:
        print(f"FAILED: synthetic extraction mismatched fields: {', '.join(failed_fields)}")
        return 1

    issue_codes = sorted({issue.code for issue in validate_invoice(invoice)})
    print(
        "PASS: live synthetic invoice extraction; "
        f"model={selection.model_id}; validation_issue_codes={','.join(issue_codes) or 'none'}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
