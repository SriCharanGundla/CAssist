"""Generate visibly marked, fabricated accounting documents for demos."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUTPUT_DIR = Path(__file__).resolve().parent / "documents"
WIDTH, HEIGHT = 1600, 2000


def font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ) if bold else (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def canvas(title: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (WIDTH, HEIGHT), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, WIDTH, 112), fill="#7f1d1d")
    draw.text(
        (45, 27),
        "SYNTHETIC DEMO — NOT A REAL DOCUMENT",
        fill="white",
        font=font(38, bold=True),
    )
    draw.text((70, 165), title, fill="#111827", font=font(58, bold=True))
    draw.line((70, 250, WIDTH - 70, 250), fill="#334155", width=3)
    return image, draw


def invoice() -> None:
    image, draw = canvas("TAX INVOICE")
    rows = (
        (300, "Invoice number", "DEMO-INV-0042"),
        (360, "Invoice date", "12 August 2026"),
        (420, "Supplier", "Example Supplies Private Limited"),
        (480, "Buyer", "Sample Retail LLP"),
        (540, "Place of supply", "Maharashtra (27)"),
    )
    for y, label, value in rows:
        draw.text((80, y), f"{label}:", fill="#334155", font=font(30, bold=True))
        draw.text((500, y), value, fill="#111827", font=font(30))
    draw.rectangle((80, 680, WIDTH - 80, 850), outline="#64748b", width=2)
    headers = ((100, "Description"), (780, "Qty"), (960, "Rate"), (1210, "Amount"))
    for x, value in headers:
        draw.text((x, 710), value, fill="#334155", font=font(28, bold=True))
    draw.text((100, 785), "Document review service", fill="#111827", font=font(27))
    draw.text((780, 785), "2", fill="#111827", font=font(27))
    draw.text((960, 785), "500.00", fill="#111827", font=font(27))
    draw.text((1210, 785), "1,000.00", fill="#111827", font=font(27))
    totals = (
        (950, "Taxable amount", "1,000.00"),
        (1010, "GST 18%", "180.00"),
        (1070, "Total INR", "1,180.00"),
    )
    for y, label, value in totals:
        draw.text((850, y), label, fill="#334155", font=font(30, bold=True))
        draw.text((1250, y), value, fill="#111827", font=font(30))
    image.save(OUTPUT_DIR / "synthetic-invoice.png", optimize=True)


def bank_statement() -> None:
    image, draw = canvas("ACCOUNT STATEMENT")
    details = (
        (300, "Account holder", "Example Trading Company"),
        (360, "Account number", "XXXX XXXX 4242"),
        (420, "Statement period", "01–31 July 2026"),
    )
    for y, label, value in details:
        draw.text((80, y), f"{label}:", fill="#334155", font=font(30, bold=True))
        draw.text((500, y), value, fill="#111827", font=font(30))
    entries = (
        ("03 Jul", "Sample office supplies", "2,450.00", ""),
        ("11 Jul", "Demo customer receipt", "", "8,900.00"),
        ("22 Jul", "Example software subscription", "1,250.00", ""),
    )
    columns = ((90, "Date"), (300, "Description"), (1030, "Debit"), (1290, "Credit"))
    for x, value in columns:
        draw.text((x, 590), value, fill="#334155", font=font(28, bold=True))
    draw.line((80, 640, WIDTH - 80, 640), fill="#64748b", width=2)
    for index, row in enumerate(entries):
        y = 690 + index * 90
        for (x, _), value in zip(columns, row, strict=True):
            draw.text((x, y), value, fill="#111827", font=font(27))
    image.save(OUTPUT_DIR / "synthetic-bank-statement.png", optimize=True)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    invoice()
    bank_statement()
    print(f"Generated synthetic demo documents in {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
