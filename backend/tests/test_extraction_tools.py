from pathlib import Path

import pytest
from PIL import Image

from app.services.extraction_tools import InvoiceExtractionWorkspace, LineItemRecord


@pytest.fixture
def page_path(tmp_path: Path) -> Path:
    path = tmp_path / "page.png"
    Image.new("RGB", (200, 100), "white").save(path)
    return path


def test_workspace_records_only_observed_fields_with_bounded_evidence(page_path: Path) -> None:
    workspace = InvoiceExtractionWorkspace((page_path,))

    workspace.record_field("/invoice_number", "INV-1", 1, 10, 10, 40, 20)
    workspace.record_field("/totals/grand_total", "118.00", 1)
    validation = workspace.validate_draft()
    invoice, evidence = workspace.result("invoice")

    assert invoice.invoice_number == "INV-1"
    assert invoice.currency is None
    assert validation["repairs_remaining"] == 1
    assert {item.field_path for item in evidence} == {
        "/invoice_number",
        "/totals/grand_total",
    }
    assert evidence[0].region is not None


def test_workspace_rejects_unregistered_fields_and_regions_outside_page(page_path: Path) -> None:
    workspace = InvoiceExtractionWorkspace((page_path,))

    with pytest.raises(ValueError, match="not registered"):
        workspace.record_field("/invented", "value", 1)
    with pytest.raises(ValueError, match="beyond the page"):
        workspace.record_field("/invoice_number", "INV-1", 1, 190, 90, 20, 20)


def test_workspace_requires_validation_and_limits_repair_pass(page_path: Path) -> None:
    workspace = InvoiceExtractionWorkspace((page_path,))

    with pytest.raises(ValueError, match="did not validate"):
        workspace.result("invoice")
    workspace.validate_draft()
    workspace.validate_draft()
    with pytest.raises(ValueError, match="Validation limit"):
        workspace.validate_draft()


def test_record_table_can_replace_a_questionable_first_pass(page_path: Path) -> None:
    workspace = InvoiceExtractionWorkspace((page_path,))
    first = LineItemRecord(description="Wrong", total="10.00")
    repaired = LineItemRecord(description="Correct", total="12.00")

    workspace.record_table([first], 1)
    workspace.record_table([repaired], 1, replace_existing=True)
    workspace.validate_draft()
    invoice, evidence = workspace.result("tax_invoice")

    assert [item.description for item in invoice.line_items] == ["Correct"]
    assert [item.field_path for item in evidence] == ["/line_items/0"]


def test_inspect_page_returns_only_the_requested_crop(page_path: Path) -> None:
    workspace = InvoiceExtractionWorkspace((page_path,))

    result = workspace.inspect_page(1, x=10, y=10, width=20, height=10, scale=2)

    image_content = result["content"][1]["image"]["source"]["bytes"]
    assert isinstance(image_content, bytes)


def test_inspect_page_rejects_whole_page_sized_reinspection(page_path: Path) -> None:
    workspace = InvoiceExtractionWorkspace((page_path,))

    with pytest.raises(ValueError, match="too large"):
        workspace.inspect_page(1, x=0, y=0, width=2000, height=1000)
