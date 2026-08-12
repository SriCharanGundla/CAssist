from copy import deepcopy
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field
from strands import tool

from app.schemas.extraction import (
    CanonicalInvoice,
    EvidenceRegion,
    FieldEvidence,
    InvoiceLineItem,
    TaxAmounts,
)
from app.services.corrections import replace_pointer
from app.services.invoice_validation import validate_invoice

_MAX_INSPECTIONS = 3
_MAX_VALIDATIONS = 2
_MAX_TOOL_CALLS = 40
_MAX_INSPECTION_PIXELS = 1_000_000

_SCALAR_PATHS = {
    "/invoice_number",
    "/invoice_date",
    "/due_date",
    "/currency",
    "/place_of_supply",
    "/reverse_charge",
    *{
        f"/{party}/{field}"
        for party in ("supplier", "buyer")
        for field in ("name", "gstin", "pan", "address", "state_code")
    },
    *{
        f"/totals/{field}"
        for field in (
            "taxable_amount",
            "discount_amount",
            "cgst_amount",
            "sgst_amount",
            "igst_amount",
            "cess_amount",
            "round_off",
            "grand_total",
        )
    },
}


class LineItemRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str | None = None
    hsn_sac: str | None = None
    quantity: str | None = None
    unit: str | None = None
    unit_price: str | None = None
    discount: str | None = None
    taxable_value: str | None = None
    gst_rate: str | None = None
    tax_amounts: TaxAmounts = Field(default_factory=TaxAmounts)
    total: str | None = None


class InvoiceExtractionWorkspace:
    """Per-document, capability-limited state exposed to the extraction agent."""

    def __init__(self, page_paths: tuple[Path, ...]) -> None:
        if not page_paths:
            raise ValueError("At least one page is required")
        self.page_paths = page_paths
        self._draft = CanonicalInvoice().model_dump(mode="json")
        self._evidence_by_path: dict[str, FieldEvidence] = {}
        self._inspection_count = 0
        self._validation_count = 0
        self._tool_call_count = 0

    def _count_call(self) -> None:
        self._tool_call_count += 1
        if self._tool_call_count > _MAX_TOOL_CALLS:
            raise ValueError("Document tool-call limit reached")

    def _page_path(self, page_number: int) -> Path:
        if page_number < 1 or page_number > len(self.page_paths):
            raise ValueError("Page number is outside this document")
        return self.page_paths[page_number - 1]

    @tool
    def inspect_page(
        self,
        page_number: int,
        x: int,
        y: int,
        width: int,
        height: int,
        scale: int = 1,
    ) -> dict[str, Any]:
        """Inspect one targeted page crop at 1x to 3x scale.

        Coordinates use pixels in the rendered page image. The complete page is already supplied
        to the agent, so this tool is only for a small ambiguous region.
        """
        self._count_call()
        self._inspection_count += 1
        if self._inspection_count > _MAX_INSPECTIONS:
            raise ValueError("Page-inspection limit reached")
        if scale < 1 or scale > 3:
            raise ValueError("Scale must be between 1 and 3")

        with Image.open(self._page_path(page_number)) as source:
            image = source.convert("RGB")
            try:
                if x < 0 or y < 0 or width <= 0 or height <= 0:
                    raise ValueError("Crop coordinates must describe a positive region")
                if width * height > _MAX_INSPECTION_PIXELS:
                    raise ValueError("Inspection crop is too large")
                if x + width > image.width or y + height > image.height:
                    raise ValueError("Crop extends beyond the page")
                cropped = image.crop((x, y, x + width, y + height))
                image.close()
                image = cropped
                if scale > 1:
                    resized = image.resize(
                        (image.width * scale, image.height * scale),
                        Image.Resampling.LANCZOS,
                    )
                    image.close()
                    image = resized
                output = BytesIO()
                image.save(output, format="PNG")
            finally:
                image.close()

        return {
            "status": "success",
            "content": [
                {"text": f"Page {page_number} inspection"},
                {"image": {"format": "png", "source": {"bytes": output.getvalue()}}},
            ],
        }

    @tool
    def record_field(
        self,
        field_path: str,
        value: str | bool,
        page_number: int,
        x: int | None = None,
        y: int | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> dict[str, str]:
        """Record one visibly supported invoice scalar using a registered JSON Pointer path.

        Page coordinates are optional, but when used all four pixel values are required.
        Never call this tool for an inferred or defaulted value.
        """
        self._count_call()
        page_path = self._page_path(page_number)
        if field_path not in _SCALAR_PATHS:
            raise ValueError("Field path is not registered for the invoice contract")
        if isinstance(value, str) and not value.strip():
            raise ValueError("Blank values are not evidence")
        if field_path == "/reverse_charge" and not isinstance(value, bool):
            raise ValueError("Reverse charge must be boolean")
        if field_path != "/reverse_charge" and not isinstance(value, str):
            raise ValueError("This field must be recorded as text")

        region = self._region(page_path, x, y, width, height)
        candidate = deepcopy(self._draft)
        replace_pointer(candidate, field_path, value)
        CanonicalInvoice.model_validate(candidate)
        self._draft = candidate
        self._evidence_by_path[field_path] = FieldEvidence(
            field_path=field_path,
            page_number=page_number,
            region=region,
        )
        return {"recorded": field_path}

    @tool
    def record_table(
        self,
        rows: list[LineItemRecord],
        page_number: int,
        x: int | None = None,
        y: int | None = None,
        width: int | None = None,
        height: int | None = None,
        replace_existing: bool = False,
    ) -> dict[str, int]:
        """Record supported line items; use replace_existing only for a validation repair."""
        self._count_call()
        page_path = self._page_path(page_number)
        if not rows:
            raise ValueError("A recorded table must contain at least one row")
        region = self._region(page_path, x, y, width, height)
        if replace_existing:
            self._draft["line_items"] = []
            self._evidence_by_path = {
                path: evidence
                for path, evidence in self._evidence_by_path.items()
                if not path.startswith("/line_items/")
            }
        start_index = len(self._draft["line_items"])
        for offset, row in enumerate(rows):
            item = InvoiceLineItem.model_validate(
                {**row.model_dump(mode="json"), "source_pages": [page_number]}
            )
            self._draft["line_items"].append(item.model_dump(mode="json"))
            path = f"/line_items/{start_index + offset}"
            self._evidence_by_path[path] = FieldEvidence(
                field_path=path,
                page_number=page_number,
                region=region,
            )
        return {"recorded_rows": len(rows)}

    @tool
    def validate_draft(self) -> dict[str, Any]:
        """Run deterministic invoice validation against all values recorded so far."""
        self._count_call()
        self._validation_count += 1
        if self._validation_count > _MAX_VALIDATIONS:
            raise ValueError("Validation limit reached")
        issues = validate_invoice(CanonicalInvoice.model_validate(self._draft))
        return {
            "valid": not issues,
            "issues": [
                {"severity": issue.severity, "code": issue.code, "field_path": issue.field_path}
                for issue in issues
            ],
            "repairs_remaining": _MAX_VALIDATIONS - self._validation_count,
        }

    @staticmethod
    def _region(
        page_path: Path,
        x: int | None,
        y: int | None,
        width: int | None,
        height: int | None,
    ) -> EvidenceRegion | None:
        values = (x, y, width, height)
        if not any(value is not None for value in values):
            return None
        if not all(value is not None for value in values):
            raise ValueError("Evidence region requires x, y, width, and height")
        assert x is not None and y is not None and width is not None and height is not None
        region = EvidenceRegion(x=x, y=y, width=width, height=height)
        with Image.open(page_path) as image:
            if region.x + region.width > image.width or region.y + region.height > image.height:
                raise ValueError("Evidence region extends beyond the page")
        return region

    def result(self, document_type: str) -> tuple[CanonicalInvoice, list[FieldEvidence]]:
        if self._validation_count < 1:
            raise ValueError("The extraction agent did not validate its recorded draft")
        draft = deepcopy(self._draft)
        draft["document_type"] = document_type
        invoice = CanonicalInvoice.model_validate(draft)
        return invoice, list(self._evidence_by_path.values())
