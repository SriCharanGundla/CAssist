import pytest

from app.services.document_text_tools import DocumentTextTools


def test_reads_one_bounded_page_and_reports_unavailable_scan() -> None:
    tools = DocumentTextTools(("Invoice number INV-1", None))

    assert tools.read_document_text(1) == {
        "page_number": 1,
        "available": True,
        "text": "Invoice number INV-1",
        "truncated": False,
    }
    assert tools.read_document_text(2) == {
        "page_number": 2,
        "available": False,
        "text": None,
    }


def test_search_is_case_insensitive_page_scoped_and_bounded() -> None:
    tools = DocumentTextTools(
        (
            "Supplier Example Private Limited\nInvoice No INV-1",
            "Payment to EXAMPLE PRIVATE LIMITED",
        )
    )

    result = tools.search_document_text("example private")

    assert [match["page_number"] for match in result["matches"]] == [1, 2]
    assert all("snippet" in match for match in result["matches"])


def test_text_tools_reject_out_of_scope_page_and_query() -> None:
    tools = DocumentTextTools((None,))

    with pytest.raises(ValueError, match="outside"):
        tools.read_document_text(2)
    with pytest.raises(ValueError, match="2 to 200"):
        tools.search_document_text("x")
